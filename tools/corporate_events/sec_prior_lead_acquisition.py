"""Acquire three separately authorized historical SEC source leads with durable limits."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import ssl
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sec_document_client as documents  # noqa: E402
import sec_timing_diagnostics as base  # noqa: E402
import sec_two_archive_ranges as ranges  # noqa: E402

sec = base.sec
ROOT, PRIVATE, PROTOCOL = base.ROOT, base.PRIVATE, base.PROTOCOL
OUTPUT = PRIVATE / "prior-eight-k-acquisition-v1"
CAP = 8_388_608
TOTAL = 25_165_824
URLS = [
    "https://www.sec.gov/Archives/edgar/data/350199/0000902664-07-002360.txt",
    "https://www.sec.gov/Archives/edgar/data/350199/0000902664-07-002366.txt",
    "https://www.sec.gov/Archives/edgar/data/896985/0001104659-08-047772.txt",
]
REGISTERED = {
    "tools/corporate_events/sec_prior_lead_acquisition.py",
    "tests/test_sec_prior_lead_acquisition.py",
    "protocols/corporate-event-alpha-v1/sec-prior-lead-acquisition-policy-v1.json",
}


def validate_manifest(entries: list[dict[str, Any]]) -> None:
    if len(entries) != 3 or [entry["url"] for entry in entries] != URLS:
        raise ValueError("PRIOR_SOURCE_EXACT_URLS_REQUIRED")
    for entry in entries:
        documents.validate_url(entry["url"], set(URLS))
        if (
            entry["form"] != "8-K"
            or not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", entry["accession"])
            or not entry["url"].endswith("/" + entry["accession"] + ".txt")
            or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", entry["filed"])
            or not re.fullmatch(r"[0-9]{1,10}", entry["index_filer_cik"])
        ):
            raise ValueError("PRIOR_SOURCE_MANIFEST_IDENTITY_INVALID")


def check_submission(raw: bytes, entry: dict[str, Any]) -> dict[str, Any]:
    documents.validate_payload(raw, entry["url"])
    parts = re.split(rb"(?im)^<DOCUMENT>[ \t]*\r?$", raw[:65536], maxsplit=1)
    if len(parts) != 2:
        raise ValueError("PRIOR_SOURCE_HEADER_BOUNDARY_MISSING")
    header = parts[0]
    expected = {
        "accession": (
            rb"(?im)^ACCESSION NUMBER:\s*([0-9]{10}-[0-9]{2}-[0-9]{6})\s*$",
            entry["accession"],
        ),
        "form": (rb"(?im)^CONFORMED SUBMISSION TYPE:\s*([^\r\n]+)", entry["form"]),
        "filed": (rb"(?im)^FILED AS OF DATE:\s*([0-9]{8})\s*$", entry["filed"].replace("-", "")),
    }
    for name, (pattern, value) in expected.items():
        matches = re.findall(pattern, header)
        if len(matches) != 1 or matches[0].decode("ascii").strip() != value:
            raise ValueError("PRIOR_SOURCE_HEADER_MISMATCH_" + name.upper())
    ciks = {
        str(int(x)) for x in re.findall(rb"(?im)^\s*CENTRAL INDEX KEY:\s*([0-9]{1,10})\s*$", header)
    }
    if str(int(entry["index_filer_cik"])) not in ciks:
        raise ValueError("PRIOR_SOURCE_INDEX_FILER_CIK_MISMATCH")
    terminal = re.search(rb"(?im)^</SEC-DOCUMENT>([^\S\r\n]*)\r?\n?([\s\S]*)\Z", raw)
    trailer = terminal[2].strip() if terminal else b"INVALID"
    trailer_ok = not trailer or (
        trailer == b"-----END PRIVACY-ENHANCED MESSAGE-----"
        and raw.startswith(b"-----BEGIN PRIVACY-ENHANCED MESSAGE-----")
    )
    starts = len(re.findall(rb"(?im)^<DOCUMENT>[ \t]*\r?$", raw))
    ends = len(re.findall(rb"(?im)^</DOCUMENT>[ \t]*\r?$", raw))
    primary = re.search(rb"(?im)^<TYPE>([^\r\n]+)", parts[1])
    if (
        not starts
        or starts != ends
        or not trailer_ok
        or primary is None
        or primary[1].decode("ascii").strip() != entry["form"]
    ):
        raise ValueError("PRIOR_SOURCE_CONTAINER_INCOMPLETE")
    return {
        "accession": entry["accession"],
        "form": entry["form"],
        "filed": entry["filed"],
        "index_filer_cik_matched": True,
        "issuer_role_verified": False,
        "document_blocks": starts,
        "event_extraction_performed": False,
        "public_time_certified": False,
    }


class Ledger:
    def __init__(self, root: Path, binding: str) -> None:
        self.root, self.path = root, root / "state.json"
        root.mkdir(parents=True, exist_ok=True)
        if self.path.with_suffix(".pending").exists():
            raise ValueError("PRIOR_SOURCE_INCOMPLETE_STATE_WRITE")
        if self.path.exists():
            self.state = json.loads(self.path.read_bytes())
            if self.state["binding"] != binding:
                raise ValueError("PRIOR_SOURCE_BINDING_CHANGED")
        else:
            if any(root.iterdir()):
                raise ValueError("PRIOR_SOURCE_LEDGER_MISSING")
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
            len(attempts) > 3
            or self.state["received_body_bytes"] > TOTAL
            or self.state["received_body_bytes"] != sum(a["bytes"] for a in attempts)
            or any(a["url"] not in URLS or not 0 <= a["bytes"] <= CAP for a in attempts)
            or len({a["url"] for a in attempts}) != len(attempts)
        ):
            raise ValueError("PRIOR_SOURCE_LEDGER_INVARIANT_FAILURE")

    def save(self) -> None:
        base.atomic_json(self.path, self.state)

    def reserve(self, url: str) -> None:
        if (
            self.state["status"] not in {"READY", "RUNNING"}
            or url not in URLS
            or len(self.state["attempts"]) >= 3
            or any(a["url"] == url for a in self.state["attempts"])
        ):
            raise ValueError("PRIOR_SOURCE_ATTEMPT_FORBIDDEN")
        self.state["status"] = "RUNNING"
        self.state["attempts"].append(
            {
                "url": url,
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
            or attempt["bytes"] + count > CAP
            or self.state["received_body_bytes"] + count > TOTAL
        ):
            raise ValueError("PRIOR_SOURCE_BYTE_CAP_VIOLATION")
        attempt["bytes"] += count
        self.state["received_body_bytes"] += count
        self.save()

    def stop(self, reason: str) -> None:
        self.state.update(status="STOPPED", stop_reason=reason)
        self.save()


def checked_headers(status: int, pairs: list[tuple[str, str]]) -> int:
    headers = {k.lower(): v for k, v in pairs}
    critical = {"content-length", "content-encoding", "transfer-encoding", "content-type"}
    seen = [k.lower() for k, _ in pairs if k.lower() in critical]
    if len(seen) != len(set(seen)):
        raise ValueError("PRIOR_SOURCE_DUPLICATE_HEADERS")
    if status != 200:
        raise ValueError(f"PRIOR_SOURCE_HTTP_{status}_STOP")
    length = headers.get("content-length", "")
    if not re.fullmatch(r"[0-9]{1,7}", length) or not 0 < int(length) <= CAP:
        raise ValueError("PRIOR_SOURCE_CONTENT_LENGTH_INVALID")
    if (
        headers.get("transfer-encoding")
        or headers.get("content-encoding", "identity").lower() != "identity"
    ):
        raise ValueError("PRIOR_SOURCE_UNSUPPORTED_ENCODING")
    if headers.get("content-type", "").split(";", 1)[0].strip().lower() not in {
        "text/plain",
        "application/octet-stream",
    }:
        raise ValueError("PRIOR_SOURCE_MEDIA_TYPE_INVALID")
    return int(length)


def receive(
    entry: dict[str, Any],
    agent: str,
    ledger: Ledger,
    context: ssl.SSLContext,
    factory: Any = http.client.HTTPSConnection,
) -> None:
    attempt = ledger.state["attempts"][-1]
    url = entry["url"]
    if (
        url not in URLS
        or attempt["url"] != url
        or attempt["outcome"] != "RESERVED"
        or attempt["status"] is not None
        or not context.check_hostname
        or context.verify_mode != ssl.CERT_REQUIRED
    ):
        raise ValueError("PRIOR_SOURCE_UNRESERVED_OR_UNVERIFIED_TRANSPORT")
    documents.validate_url(url, set(URLS))
    path = ledger.root / f"response-{len(ledger.state['attempts']):02}.part"
    token = sec.NETWORK_HOST.set("www.sec.gov")
    connection = None
    try:
        connection = factory("www.sec.gov", 443, timeout=30, context=context)
        connection.request(
            "GET",
            urlsplit(url).path,
            headers={
                "User-Agent": agent,
                "Accept": "text/plain,application/octet-stream",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        pairs = response.getheaders()
        attempt["status"] = response.status
        allowed = {
            "content-length",
            "content-type",
            "content-encoding",
            "transfer-encoding",
            "date",
            "retry-after",
        }
        attempt["headers"] = {k.lower(): v for k, v in pairs if k.lower() in allowed}
        ledger.save()
        remaining = checked_headers(response.status, pairs)
        with path.open("xb") as out:
            while remaining:
                count = min(65536, remaining)
                try:
                    block = response.read(count)
                except http.client.IncompleteRead as error:
                    ledger.charge(len(error.partial))
                    out.write(error.partial)
                    out.flush()
                    os.fsync(out.fileno())
                    raise ValueError("PRIOR_SOURCE_PARTIAL_RESPONSE_STOP") from None
                ledger.charge(len(block))
                out.write(block)
                if not block or len(block) > count:
                    raise ValueError("PRIOR_SOURCE_TRUNCATED_OR_OVERSIZE_READ")
                remaining -= len(block)
            out.flush()
            os.fsync(out.fileno())
        if not response.isclosed():
            raise ValueError("PRIOR_SOURCE_RESPONSE_END_UNVERIFIED")
        raw = path.read_bytes()
        proof = check_submission(raw, entry)
        cached = sec.Cache(ledger.root).store(url, raw, "text/plain")
        path.unlink()
        attempt.update(
            outcome="SOURCE_VALIDATED",
            sha256=cached["sha256"],
            locator=cached["object_locator"],
            validation=proof,
        )
        ledger.save()
    finally:
        if connection is not None:
            connection.close()
        sec.NETWORK_HOST.reset(token)


def verify_prior() -> None:
    prior = json.loads((PRIVATE / "prior-eight-k-prior-attempts-v1.json").read_bytes())
    current = json.loads((PRIVATE / "scheduler-state.json").read_bytes())["attempts_by_url"]
    if (
        any(current.get(k) != v for k, v in prior.items() if k not in URLS)
        or set(current) - set(prior) - set(URLS)
        or any(current.get(url, 0) not in {0, 1} for url in URLS)
    ):
        raise ValueError("PRIOR_SOURCE_PRIOR_ATTEMPTS_CHANGED")


def preflight() -> tuple[dict[str, Any], str]:
    sec.preflight()
    registry = json.loads((PROTOCOL / "sec-prior-lead-acquisition-extension-v1.json").read_bytes())
    if set(registry["files"]) != REGISTERED:
        raise ValueError("PRIOR_SOURCE_REGISTRY_SCOPE_INVALID")
    for name, digest in registry["files"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("PRIOR_SOURCE_REGISTERED_FILE_CHANGED")
    policy = json.loads((PROTOCOL / "sec-prior-lead-acquisition-policy-v1.json").read_bytes())
    for name, digest in policy["private_inputs"].items():
        if ranges.sha_file(PRIVATE / name) != digest:
            raise ValueError("PRIOR_SOURCE_PINNED_INPUT_CHANGED")
    for name, digest in policy["dependencies"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("PRIOR_SOURCE_DEPENDENCY_CHANGED")
    plan: dict[str, Any] = json.loads(
        (PRIVATE / "prior-eight-k-source-manifest-v1.json").read_bytes()
    )
    validate_manifest(plan["entries"])
    verify_prior()
    plan["policy"], plan["registry"] = policy, registry
    return plan, sec.sha(base.canonical(registry))


def safe_stop(ledger: Ledger, reason: str) -> None:
    try:
        ledger.stop(reason)
    except Exception as error:
        incident = {
            "original_stop_reason": reason,
            "state_write_error_type": type(error).__name__,
            "original_state_or_pending_preserved": True,
        }
        with suppress(Exception):
            sec.write_new(ledger.root / "state-write-incident.json", base.canonical(incident))
        raise ValueError("PRIOR_SOURCE_STATE_WRITE_INCOMPLETE_ORIGINAL_" + reason) from error


def run(command: str) -> dict[str, Any]:
    plan, binding = preflight()
    if command == "validate":
        contact = sec.private_contact(ROOT)
        del contact
        return {
            "status": "OFFLINE_READY_AWAITING_EXPLICIT_APPROVAL",
            "request_cap": 3,
            "per_response_byte_cap": CAP,
            "new_body_byte_cap": TOTAL,
            "contact_configured": True,
            "network_requests": 0,
            "registry_binding": binding,
        }
    auth = PRIVATE / "prior-eight-k-authorization-v1.json"
    if not auth.exists():
        raise ValueError("PRIOR_SOURCE_EXPLICIT_APPROVAL_REQUIRED")
    approval = json.loads(auth.read_bytes())
    if (
        approval.get("approved") is not True
        or approval.get("registry_binding") != binding
        or approval.get("scope") != "THREE_PRIOR_EIGHT_K_SOURCES_ONLY"
        or not approval.get("verbatim_user_approval")
    ):
        raise ValueError("PRIOR_SOURCE_EXPLICIT_APPROVAL_REQUIRED")
    ledger_binding = sec.sha(base.canonical({"registry": binding, "approval": approval}))
    context, agent = None, None
    if command == "acquire":
        agent = "CorporateEventAlphaResearch/prior-eight-k-v1 (" + sec.private_contact(ROOT) + ")"
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    sec.EXTENSION_FILES = (*sec.EXTENSION_FILES, *REGISTERED, *plan["policy"]["dependencies"])
    sec.install_firewall()
    if command == "audit":
        sys.addaudithook(
            lambda event, args: (
                (_ for _ in ()).throw(RuntimeError("PRIOR_SOURCE_AUDIT_OFFLINE_ONLY"))
                if event.startswith(("socket.", "http.client."))
                else None
            )
        )
        if not (OUTPUT / "state.json").exists():
            raise ValueError("PRIOR_SOURCE_AUDIT_REQUIRES_LEDGER")
        ledger = Ledger(OUTPUT, ledger_binding)
    else:
        assert context and agent
        with sec.Scheduler(PRIVATE) as scheduler:
            ledger = Ledger(OUTPUT, ledger_binding)
            if ledger.state["status"] == "RUNNING":
                safe_stop(ledger, "PRIOR_SOURCE_INTERRUPTED_NO_AUTOMATIC_RESUME")
            if ledger.state["status"] == "READY":
                try:
                    for entry in plan["entries"]:
                        if scheduler.attempts_used(entry["url"]) != 0:
                            raise ValueError("PRIOR_SOURCE_GLOBAL_ATTEMPT_ALREADY_SPENT")
                        scheduler.start(time.time() + 300)
                        ledger.reserve(entry["url"])
                        scheduler.record_attempt(entry["url"])
                        receive(entry, agent, ledger, context)
                        print(
                            json.dumps(
                                {
                                    "source_validated": len(ledger.state["attempts"]),
                                    "received_body_bytes": ledger.state["received_body_bytes"],
                                }
                            ),
                            flush=True,
                        )
                    ledger.state["status"] = "COMPLETED_SOURCE_VALIDATION_ONLY"
                    ledger.save()
                except Exception as error:
                    reason = base.safe_reason(error)
                    if (
                        ledger.state["attempts"]
                        and ledger.state["attempts"][-1]["outcome"] == "RESERVED"
                    ):
                        attempt = ledger.state["attempts"][-1]
                        attempt["outcome"] = reason
                        fragment = OUTPUT / f"response-{len(ledger.state['attempts']):02}.part"
                        if fragment.exists():
                            attempt["partial_locator"] = fragment.name
                            attempt["partial_sha256"] = ranges.sha_file(fragment)
                        if attempt["status"] == 429:
                            scheduler.hold(
                                sec.retry_after_seconds(
                                    attempt["headers"].get("retry-after"), sec.utc_now()
                                ),
                                reason,
                            )
                        if attempt["status"] in {403, 429}:
                            scheduler.block(reason)
                    safe_stop(ledger, reason)
    verify_prior()
    for attempt in ledger.state["attempts"]:
        if attempt["outcome"] == "SOURCE_VALIDATED":
            raw = sec.Cache(OUTPUT).read(attempt["sha256"])
            entry = next(x for x in plan["entries"] if x["url"] == attempt["url"])
            if (
                len(raw) != attempt["bytes"]
                or check_submission(raw, entry) != attempt["validation"]
            ):
                raise ValueError("PRIOR_SOURCE_CACHE_REPLAY_FAILURE")
    result = {
        "status": ledger.state["status"],
        "stop_reason": ledger.state["stop_reason"],
        "HTTP_attempts": len(ledger.state["attempts"]),
        "received_body_bytes": ledger.state["received_body_bytes"],
        "validated_sources": sum(
            a["outcome"] == "SOURCE_VALIDATED" for a in ledger.state["attempts"]
        ),
        "event_extraction_performed": False,
        "public_times_certified": 0,
        "prior_budgets_reset": 0,
        "regressions_run": 0,
        "original_718_sample_changes": 0,
        "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
    }
    base.immutable(OUTPUT / "summary.json", result)
    base.immutable(
        OUTPUT / "completion-receipt.json",
        {
            "state_sha256": sec.file_sha(OUTPUT / "state.json"),
            "summary_sha256": sec.file_sha(OUTPUT / "summary.json"),
            "binding": ledger_binding,
            "registry": plan["registry"],
        },
    )
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("command", choices=["validate", "acquire", "audit"])
    try:
        print(json.dumps(run(cli.parse_args().command)), flush=True)
    except Exception as error:
        print(json.dumps({"status": "PRIOR_SOURCE_STOPPED", "reason": base.safe_reason(error)}))
        raise SystemExit(2) from None
