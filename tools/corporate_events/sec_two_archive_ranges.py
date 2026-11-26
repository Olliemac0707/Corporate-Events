"""Complete two version-pinned SEC archives under the frozen range and attempt limits."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import http.client
import json
import os
import re
import shutil
import ssl
import sys
import tarfile
import time
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sec_timing_diagnostics as base  # noqa: E402

sec = base.sec
ROOT, PRIVATE, PROTOCOL = base.ROOT, base.PRIVATE, base.PROTOCOL
OUTPUT = PRIVATE / "two-archive-ranges-v1"
INSPECTION = PRIVATE / "two-archive-inspection-v1"
PREFIX = 1_048_576
RANGE_CAP = 52_428_800
TOTAL_CAP = 365_284_503
DECODED_CAP = 8_589_934_592
TARGET_CAP = 33_554_432
URLS = [
    "https://www.sec.gov/Archives/edgar/Feed/2007/QTR3/20070802.nc.tar.gz",
    "https://www.sec.gov/Archives/edgar/Feed/2008/QTR3/20080730.nc.tar.gz",
]
SIZES = [171_106_207, 196_275_448]
ACCESSIONS = ["0000902664-07-002404", "0001104659-08-048595"]
REGISTERED = {
    "tools/corporate_events/sec_two_archive_ranges.py",
    "tests/test_sec_two_archive_ranges.py",
    "protocols/corporate-event-alpha-v1/sec-two-archive-ranges-policy-v1.json",
}


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1_048_576):
            digest.update(block)
    return digest.hexdigest()


def ranges(total: int) -> list[tuple[int, int]]:
    return [(start, min(total, start + RANGE_CAP) - 1) for start in range(PREFIX, total, RANGE_CAP)]


def validate_targets(targets: list[dict[str, Any]]) -> None:
    if len(targets) != 2:
        raise ValueError("TWO_ARCHIVE_TARGET_COUNT_INVALID")
    for i, target in enumerate(targets):
        if (
            target["url"] != URLS[i]
            or target["total_bytes"] != SIZES[i]
            or target["accession"] != ACCESSIONS[i]
            or not re.fullmatch(r'"[A-Za-z0-9-]+"', target["etag"])
            or not re.fullmatch(r"[0-9a-f]{64}", target["prefix_sha256"])
            or not re.fullmatch(r"[0-9a-f]{64}", target["source_sha256"])
            or len(ranges(target["total_bytes"])) != 4
        ):
            raise ValueError("TWO_ARCHIVE_TARGET_IDENTITY_INVALID")
    if sum(t["total_bytes"] - PREFIX for t in targets) != TOTAL_CAP:
        raise ValueError("TWO_ARCHIVE_BYTE_SCOPE_INVALID")


def checked_headers(
    status: int, pairs: list[tuple[str, str]], target: dict[str, Any], start: int, end: int
) -> dict[str, str]:
    headers = {k.lower(): v for k, v in pairs}
    critical = {
        "content-range",
        "content-length",
        "content-type",
        "content-encoding",
        "transfer-encoding",
        "etag",
        "last-modified",
    }
    seen = [k.lower() for k, _ in pairs if k.lower() in critical]
    if len(seen) != len(set(seen)):
        raise ValueError("TWO_ARCHIVE_DUPLICATE_HEADERS")
    if status != 206:
        raise ValueError(f"TWO_ARCHIVE_HTTP_{status}_STOP")
    if (
        headers.get("content-range") != f"bytes {start}-{end}/{target['total_bytes']}"
        or headers.get("content-length") != str(end - start + 1)
        or headers.get("etag") != target["etag"]
        or headers.get("last-modified") != target["last_modified"]
    ):
        raise ValueError("TWO_ARCHIVE_REPRESENTATION_OR_RANGE_CHANGED")
    if (
        headers.get("transfer-encoding")
        or headers.get("content-encoding", "identity").lower() != "identity"
    ):
        raise ValueError("TWO_ARCHIVE_UNSUPPORTED_ENCODING")
    mime = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if mime not in {
        "application/x-tar",
        "application/gzip",
        "application/x-gzip",
        "application/octet-stream",
        "binary/octet-stream",
        "application/x-compressed",
    }:
        raise ValueError("TWO_ARCHIVE_MEDIA_TYPE_INVALID")
    return headers


class Ledger:
    def __init__(self, root: Path, binding: str) -> None:
        self.root, self.path = root, root / "state.json"
        root.mkdir(parents=True, exist_ok=True)
        if self.path.with_suffix(".pending").exists():
            raise ValueError("TWO_ARCHIVE_INCOMPLETE_STATE_WRITE")
        if self.path.exists():
            self.state = json.loads(self.path.read_bytes())
            if self.state["binding"] != binding:
                raise ValueError("TWO_ARCHIVE_LEDGER_BINDING_CHANGED")
        else:
            if any(root.iterdir()):
                raise ValueError("TWO_ARCHIVE_LEDGER_MISSING")
            self.state = {
                "binding": binding,
                "status": "READY",
                "stop_reason": None,
                "attempts": [],
                "received_body_bytes": 0,
            }
            self.save()
        attempts = self.state["attempts"]
        if (
            len(attempts) > 8
            or self.state["received_body_bytes"] > TOTAL_CAP
            or self.state["received_body_bytes"] != sum(a["bytes"] for a in attempts)
            or any(a["url"] not in URLS or not 0 <= a["bytes"] <= RANGE_CAP for a in attempts)
            or any(sum(a["url"] == url for a in attempts) > 4 for url in URLS)
            or len({(a["url"], a["start"], a["end"]) for a in attempts}) != len(attempts)
        ):
            raise ValueError("TWO_ARCHIVE_LEDGER_INVARIANT_FAILURE")

    def save(self) -> None:
        base.atomic_json(self.path, self.state)

    def reserve(self, url: str, start: int, end: int) -> None:
        if (
            self.state["status"] not in {"READY", "RUNNING"}
            or url not in URLS
            or (start, end) not in ranges(SIZES[URLS.index(url)])
            or len(self.state["attempts"]) >= 8
            or sum(a["url"] == url for a in self.state["attempts"]) >= 4
            or any(
                (a["url"], a["start"], a["end"]) == (url, start, end)
                for a in self.state["attempts"]
            )
        ):
            raise ValueError("TWO_ARCHIVE_ATTEMPT_FORBIDDEN")
        self.state["status"] = "RUNNING"
        self.state["attempts"].append(
            {
                "url": url,
                "start": start,
                "end": end,
                "bytes": 0,
                "status": None,
                "headers": {},
                "outcome": "RESERVED",
                "sha256": None,
                "locator": None,
            }
        )
        self.save()

    def charge(self, count: int) -> None:
        attempt = self.state["attempts"][-1]
        if (
            type(count) is not int
            or count < 0
            or attempt["bytes"] + count > RANGE_CAP
            or attempt["bytes"] + count > attempt["end"] - attempt["start"] + 1
            or self.state["received_body_bytes"] + count > TOTAL_CAP
        ):
            self.stop("TWO_ARCHIVE_BYTE_CAP_VIOLATION")
            raise ValueError("TWO_ARCHIVE_BYTE_CAP_VIOLATION")
        attempt["bytes"] += count
        self.state["received_body_bytes"] += count
        self.save()

    def stop(self, reason: str) -> None:
        self.state.update(status="STOPPED", stop_reason=reason)
        self.save()


def receive(
    target: dict[str, Any],
    start: int,
    end: int,
    agent: str,
    ledger: Ledger,
    context: ssl.SSLContext,
    factory: Any = http.client.HTTPSConnection,
) -> None:
    attempt = ledger.state["attempts"][-1]
    if (
        attempt["url"] != target["url"]
        or attempt["outcome"] != "RESERVED"
        or attempt["status"] is not None
        or (attempt["start"], attempt["end"]) != (start, end)
        or target["url"] not in URLS
        or not context.check_hostname
        or context.verify_mode != ssl.CERT_REQUIRED
    ):
        raise ValueError("TWO_ARCHIVE_UNRESERVED_OR_UNVERIFIED_TRANSPORT")
    parsed = urlsplit(target["url"])
    path = ledger.root / f"range-{len(ledger.state['attempts']):02}.part"
    token = sec.NETWORK_HOST.set("www.sec.gov")
    connection = None
    try:
        connection = factory("www.sec.gov", 443, timeout=30, context=context)
        connection.request(
            "GET",
            parsed.path,
            headers={
                "User-Agent": agent,
                "Accept": "application/octet-stream",
                "Accept-Encoding": "identity",
                "Connection": "close",
                "If-Range": target["etag"],
                "Range": f"bytes={start}-{end}",
            },
        )
        response = connection.getresponse()
        pairs = response.getheaders()
        attempt["status"] = response.status
        allowed = {
            "content-range",
            "content-length",
            "content-type",
            "content-encoding",
            "transfer-encoding",
            "etag",
            "last-modified",
            "date",
            "retry-after",
        }
        attempt["headers"] = {k.lower(): v for k, v in pairs if k.lower() in allowed}
        ledger.save()
        checked_headers(response.status, pairs, target, start, end)
        remaining = end - start + 1
        with path.open("xb") as out:
            while remaining:
                count = min(1_048_576, remaining)
                try:
                    block = response.read(count)
                except http.client.IncompleteRead as error:
                    ledger.charge(len(error.partial))
                    out.write(error.partial)
                    out.flush()
                    os.fsync(out.fileno())
                    raise ValueError("TWO_ARCHIVE_PARTIAL_RESPONSE_STOP") from None
                ledger.charge(len(block))
                out.write(block)
                if not block or len(block) > count:
                    raise ValueError("TWO_ARCHIVE_TRUNCATED_OR_OVERSIZE_READ")
                remaining -= len(block)
            out.flush()
            os.fsync(out.fileno())
        if not response.isclosed():
            raise ValueError("TWO_ARCHIVE_RESPONSE_END_UNVERIFIED")
        digest = sha_file(path)
        obj = ledger.root / "objects" / "sha256" / digest[:2] / digest
        obj.parent.mkdir(parents=True, exist_ok=True)
        if obj.exists():
            if sha_file(obj) != digest:
                raise ValueError("TWO_ARCHIVE_CACHE_CONFLICT")
            path.unlink()
        else:
            os.replace(path, obj)
        attempt.update(
            outcome="RANGE_CACHED",
            sha256=digest,
            locator=str(obj.relative_to(ledger.root)).replace("\\", "/"),
        )
        ledger.save()
    finally:
        if connection is not None:
            connection.close()
        sec.NETWORK_HOST.reset(token)


def assemble(target: dict[str, Any], ledger: Ledger, prefix: bytes, destination: Path) -> Path:
    if len(prefix) != PREFIX or sec.sha(prefix) != target["prefix_sha256"]:
        raise ValueError("TWO_ARCHIVE_PREFIX_CHANGED")
    selected = [a for a in ledger.state["attempts"] if a["url"] == target["url"]]
    if [(a["start"], a["end"]) for a in selected] != ranges(target["total_bytes"]) or any(
        a["outcome"] != "RANGE_CACHED" for a in selected
    ):
        raise ValueError("TWO_ARCHIVE_COVERAGE_INCOMPLETE")
    destination.mkdir(parents=True, exist_ok=True)
    temp = destination / (target["accession"] + ".gz.part")
    with temp.open("xb") as output:
        output.write(prefix)
        for attempt in selected:
            source = ledger.root / attempt["locator"]
            if source.stat().st_size != attempt["bytes"] or sha_file(source) != attempt["sha256"]:
                raise ValueError("TWO_ARCHIVE_CACHED_RANGE_CHANGED")
            with source.open("rb") as stream:
                shutil.copyfileobj(stream, output, length=1_048_576)
        output.flush()
        os.fsync(output.fileno())
    if temp.stat().st_size != target["total_bytes"]:
        raise ValueError("TWO_ARCHIVE_ASSEMBLED_SIZE_INVALID")
    digest = sha_file(temp)
    final = destination / "archives" / (digest + ".gz")
    final.parent.mkdir(parents=True, exist_ok=True)
    if final.exists():
        if sha_file(final) != digest:
            raise ValueError("TWO_ARCHIVE_ASSEMBLED_CACHE_CONFLICT")
        temp.unlink()
    else:
        os.replace(temp, final)
    return final


def inspect(path: Path, target: dict[str, Any], source: bytes) -> dict[str, Any]:
    decoder = zlib.decompressobj(31)
    decoded = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1_048_576):
            pending = chunk
            while pending:
                try:
                    block = decoder.decompress(pending, min(1_048_576, DECODED_CAP + 1 - decoded))
                except zlib.error:
                    raise ValueError("TWO_ARCHIVE_GZIP_INTEGRITY_FAILURE") from None
                decoded += len(block)
                if decoded > DECODED_CAP or decoder.unused_data:
                    raise ValueError("TWO_ARCHIVE_GZIP_SIZE_OR_TRAILING_DATA_INVALID")
                pending = decoder.unconsumed_tail
    if not decoder.eof:
        raise ValueError("TWO_ARCHIVE_GZIP_TRUNCATED")
    if sec.sha(source) != target["source_sha256"]:
        raise ValueError("TWO_ARCHIVE_ORIGINAL_FILING_CHANGED")
    wanted = target["accession"]
    found: list[dict[str, Any]] = []
    headers = 0
    try:
        with tarfile.open(path, mode="r|gz") as archive:
            for member in archive:
                headers += 1
                if member.name not in {wanted + ".nc", "./" + wanted + ".nc"}:
                    continue
                if found or not member.isfile() or member.size > TARGET_CAP:
                    raise ValueError("TWO_ARCHIVE_TARGET_DUPLICATE_TYPE_OR_SIZE_INVALID")
                member_stream = archive.extractfile(member)
                if member_stream is None:
                    raise ValueError("TWO_ARCHIVE_TARGET_NOT_READABLE")
                body = member_stream.read(TARGET_CAP + 1)
                if len(body) != member.size:
                    raise ValueError("TWO_ARCHIVE_TARGET_BODY_SIZE_INVALID")
                header = body[:65536].split(b"<DOCUMENT>", 1)[0]
                ids = re.findall(
                    rb"(?im)^(?:<ACCESSION-NUMBER>\s*|ACCESSION NUMBER:\s*)"
                    rb"([0-9]{10}-[0-9]{2}-[0-9]{6})\s*$",
                    header,
                )
                if not ids or set(ids) != {wanted.encode()}:
                    raise ValueError("TWO_ARCHIVE_TARGET_INTERNAL_ACCESSION_MISMATCH")
                found.append(
                    {
                        "accession": wanted,
                        "member_name": member.name,
                        "member_bytes": len(body),
                        "member_sha256": sec.sha(body),
                        "original_source_sha256": target["source_sha256"],
                        "exact_raw_filing_match": body == source,
                        "internal_accession_verified": True,
                    }
                )
    except (tarfile.TarError, EOFError, gzip.BadGzipFile) as error:
        raise ValueError("TWO_ARCHIVE_TAR_PARSE_FAILURE") from error
    return {
        "archive_sha256": sha_file(path),
        "archive_bytes": path.stat().st_size,
        "decoded_bytes": decoded,
        "gzip_eof_and_checksum_verified": True,
        "tar_headers_checked": headers,
        "matches": found,
        "target_found": bool(found),
        "unrelated_filing_bodies_extracted": 0,
        "public_times_certified": 0,
    }


def preflight() -> tuple[dict[str, Any], str]:
    sec.preflight()
    registry = json.loads((PROTOCOL / "sec-two-archive-ranges-extension-v1.json").read_bytes())
    if set(registry["files"]) != REGISTERED:
        raise ValueError("TWO_ARCHIVE_REGISTRY_SCOPE_INVALID")
    for name, digest in registry["files"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("TWO_ARCHIVE_REGISTERED_FILE_CHANGED")
    policy = json.loads((PROTOCOL / "sec-two-archive-ranges-policy-v1.json").read_bytes())
    for name, digest in policy["private_inputs"].items():
        if sec.file_sha(PRIVATE / name) != digest:
            raise ValueError("TWO_ARCHIVE_PINNED_INPUT_CHANGED")
    for name, digest in policy["dependencies"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("TWO_ARCHIVE_DEPENDENCY_CHANGED")
    plan: dict[str, Any] = json.loads((PRIVATE / "two-archive-ranges-plan-v1.json").read_bytes())
    validate_targets(plan["targets"])
    verify_prior(plan)
    plan["policy"], plan["registry"] = policy, registry
    return plan, sec.sha(base.canonical(registry))


def verify_prior(plan: dict[str, Any]) -> None:
    old = json.loads((PRIVATE / "two-archive-prior-attempts-v1.json").read_bytes())
    current = json.loads((PRIVATE / "scheduler-state.json").read_bytes())["attempts_by_url"]
    if (
        set(current) != set(old)
        or any(current[k] != n for k, n in old.items() if k not in URLS)
        or any(old.get(url) != 1 or current[url] not in range(1, 6) for url in URLS)
    ):
        raise ValueError("TWO_ARCHIVE_PRIOR_ATTEMPTS_CHANGED")


def run(command: str) -> dict[str, Any]:
    plan, binding = preflight()
    if command == "validate":
        contact = sec.private_contact(ROOT)
        del contact
        if shutil.disk_usage(PRIVATE).free < 1_500_000_000:
            raise ValueError("TWO_ARCHIVE_INSUFFICIENT_DISK_SPACE")
        return {
            "status": "OFFLINE_PREFLIGHT_PASS",
            "request_cap": 8,
            "new_body_byte_cap": TOTAL_CAP,
            "contact_configured": True,
            "network_requests": 0,
            "registry_binding": binding,
        }
    approval = json.loads((PRIVATE / "two-archive-ranges-authorization-v1.json").read_bytes())
    if (
        approval.get("approved") is not True
        or approval.get("registry_binding") != binding
        or approval.get("scope") != "TWO_ARCHIVES_EIGHT_RANGES_ONLY"
    ):
        raise ValueError("TWO_ARCHIVE_AUTHORIZATION_REQUIRED")
    ledger_binding = sec.sha(base.canonical({"registry": binding, "approval": approval}))
    context, agent = None, None
    if command == "acquire":
        agent = (
            "CorporateEventAlphaResearch/two-archive-ranges-v1 (" + sec.private_contact(ROOT) + ")"
        )
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    sec.EXTENSION_FILES = (*sec.EXTENSION_FILES, *REGISTERED, *plan["policy"]["dependencies"])
    sec.install_firewall()
    if command in {"audit", "inspect"}:
        sys.addaudithook(
            lambda event, args: (
                (_ for _ in ()).throw(RuntimeError("TWO_ARCHIVE_AUDIT_OFFLINE_ONLY"))
                if event.startswith(("socket.", "http.client."))
                else None
            )
        )
        if not (OUTPUT / "state.json").exists():
            raise ValueError("TWO_ARCHIVE_AUDIT_REQUIRES_LEDGER")
        ledger = Ledger(OUTPUT, ledger_binding)
    else:
        assert context and agent
        with sec.Scheduler(PRIVATE) as scheduler:
            ledger = Ledger(OUTPUT, ledger_binding)
            if ledger.state["status"] == "RUNNING":
                ledger.stop("TWO_ARCHIVE_INTERRUPTED_NO_AUTOMATIC_RESUME")
            if ledger.state["status"] == "READY":
                try:
                    for target in plan["targets"]:
                        for start, end in ranges(target["total_bytes"]):
                            expected = 1 + sum(
                                a["url"] == target["url"] for a in ledger.state["attempts"]
                            )
                            if scheduler.attempts_used(target["url"]) != expected or expected >= 5:
                                raise ValueError("TWO_ARCHIVE_GLOBAL_ATTEMPT_LIMIT")
                            scheduler.start(time.time() + 1800)
                            ledger.reserve(target["url"], start, end)
                            scheduler.record_attempt(target["url"])
                            receive(target, start, end, agent, ledger, context)
                            print(
                                json.dumps(
                                    {
                                        "range_completed": len(ledger.state["attempts"]),
                                        "received_body_bytes": ledger.state["received_body_bytes"],
                                    }
                                ),
                                flush=True,
                            )
                    ledger.state["status"] = "RANGES_COMPLETE_PENDING_OFFLINE_VALIDATION"
                    ledger.save()
                except Exception as error:
                    reason = base.safe_reason(error)
                    if (
                        ledger.state["attempts"]
                        and ledger.state["attempts"][-1]["outcome"] == "RESERVED"
                    ):
                        attempt = ledger.state["attempts"][-1]
                        attempt["outcome"] = reason
                        fragment = OUTPUT / f"range-{len(ledger.state['attempts']):02}.part"
                        if fragment.exists():
                            attempt["partial_locator"] = fragment.name
                            attempt["partial_sha256"] = sha_file(fragment)
                        if attempt["status"] == 429:
                            scheduler.hold(
                                sec.retry_after_seconds(
                                    attempt["headers"].get("retry-after"), sec.utc_now()
                                ),
                                reason,
                            )
                        if attempt["status"] in {403, 429}:
                            scheduler.block(reason)
                    ledger.stop(reason)
    verify_prior(plan)
    attempts = ledger.state["attempts"]
    for attempt in attempts:
        if attempt["outcome"] == "RANGE_CACHED":
            path = OUTPUT / attempt["locator"]
            if sha_file(path) != attempt["sha256"] or path.stat().st_size != attempt["bytes"]:
                raise ValueError("TWO_ARCHIVE_CACHE_REPLAY_FAILURE")
    if command == "inspect":
        if ledger.state["status"] != "RANGES_COMPLETE_PENDING_OFFLINE_VALIDATION":
            raise ValueError("TWO_ARCHIVE_INSPECTION_REQUIRES_COMPLETE_RANGES")
        proofs = []
        for target in plan["targets"]:
            prefix = sec.Cache(PRIVATE / "repurchase-timing-diagnostics-v1").read(
                target["prefix_sha256"]
            )
            source = sec.Cache(PRIVATE / "repurchase-acquisition-v1").read(target["source_sha256"])
            archive = assemble(target, ledger, prefix, INSPECTION)
            proofs.append(inspect(archive, target, source))
        result = {
            "status": "OFFLINE_ARCHIVE_MEMBERSHIP_CHECK_COMPLETE",
            "archives_checked": len(proofs),
            "target_members_found": sum(p["target_found"] for p in proofs),
            "proofs": proofs,
            "new_HTTP_requests": 0,
            "public_times_certified": 0,
            "unrelated_filing_bodies_extracted": 0,
            "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
        }
        base.immutable(INSPECTION / "summary.json", result)
        base.immutable(
            INSPECTION / "receipt.json",
            {
                "summary_sha256": sec.file_sha(INSPECTION / "summary.json"),
                "source_state_sha256": sec.file_sha(OUTPUT / "state.json"),
                "registry": plan["registry"],
            },
        )
        return result
    summary = {
        "status": ledger.state["status"],
        "stop_reason": ledger.state["stop_reason"],
        "HTTP_attempts": len(attempts),
        "received_body_bytes": ledger.state["received_body_bytes"],
        "HTTP_statuses": [a["status"] for a in attempts],
        "cached_complete_ranges": sum(a["outcome"] == "RANGE_CACHED" for a in attempts),
        "public_times_certified": 0,
        "prior_budgets_reset": 0,
        "unrelated_filing_bodies_extracted": 0,
        "regressions_run": 0,
        "original_718_sample_changes": 0,
        "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
    }
    base.immutable(OUTPUT / "summary.json", summary)
    base.immutable(
        OUTPUT / "completion-receipt.json",
        {
            "state_sha256": sec.file_sha(OUTPUT / "state.json"),
            "summary_sha256": sec.file_sha(OUTPUT / "summary.json"),
            "binding": ledger_binding,
            "registry": plan["registry"],
        },
    )
    return summary


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("command", choices=["validate", "acquire", "audit", "inspect"])
    try:
        print(json.dumps(run(cli.parse_args().command)), flush=True)
    except Exception as error:
        print(json.dumps({"status": "TWO_ARCHIVE_STOPPED", "reason": base.safe_reason(error)}))
        raise SystemExit(2) from None
