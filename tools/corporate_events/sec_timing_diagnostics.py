"""Four exact SEC archive-prefix diagnostics with durable one-attempt budgets."""

from __future__ import annotations

import argparse
import http.client
import importlib.util
import json
import os
import re
import ssl
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "protocols/corporate-event-alpha-v1"
PRIVATE = ROOT / "reports/corporate-event-alpha-v1/phase2-private"
OUTPUT = PRIVATE / "repurchase-timing-diagnostics-v1"
CAP = 1_048_576
TOTAL = 4 * CAP
REGISTERED = {
    "tools/corporate_events/sec_timing_diagnostics.py",
    "tests/test_sec_timing_diagnostics.py",
    "protocols/corporate-event-alpha-v1/sec-timing-diagnostics-policy-v1.json",
}


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools/corporate_events" / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sec = load("timing_sec", "sec_census.py")
archive = load("timing_archive", "sec_archive_membership.py")


def canonical(data: Any) -> bytes:
    return (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode()


def atomic_json(path: Path, data: Any) -> None:
    pending = path.with_suffix(".pending")
    with pending.open("xb") as stream:
        stream.write(canonical(data))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(pending, path)


def validate_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    limits = plan["limits"]
    required = {
        "maximum_HTTP_requests_including_failures": 4,
        "maximum_attempts_per_URL": 1,
        "maximum_received_body_bytes_total": TOTAL,
        "maximum_received_body_bytes_per_response": CAP,
        "maximum_decoded_bytes_per_prefix": 33_554_432,
        "minimum_request_start_spacing_seconds": 0.5,
        "concurrency": 1,
        "socket_timeout_seconds": 30,
        "redirects_allowed": False,
        "automatic_retries_allowed": False,
        "whole_archive_download_allowed": False,
        "old_budget_resets_allowed": False,
    }
    if limits != required:
        raise ValueError("TIMING_APPROVED_LIMITS_CHANGED")
    targets: list[dict[str, Any]] = plan["targets"]
    if len(targets) != 4 or len({t["url"] for t in targets}) != 4:
        raise ValueError("TIMING_EXACT_FOUR_URLS_REQUIRED")
    accessions = []
    for target in targets:
        parsed = urlsplit(target["url"])
        match = re.fullmatch(
            r"/Archives/edgar/Feed/(200[6-9]|201[0-5])/QTR([1-4])/([0-9]{8})\.nc\.tar\.gz",
            parsed.path,
        )
        if (
            parsed.scheme != "https"
            or parsed.netloc != "www.sec.gov"
            or not match
            or parsed.query
            or parsed.fragment
            or target["range"] != "bytes=0-1048575"
        ):
            raise ValueError("TIMING_EXACT_SEC_ARCHIVE_URL_REQUIRED")
        day = time.strptime(match[3], "%Y%m%d")
        if day.tm_year != int(match[1]) or (day.tm_mon - 1) // 3 + 1 != int(match[2]):
            raise ValueError("TIMING_ARCHIVE_DATE_QUARTER_MISMATCH")
        for accession in target["target_accessions"]:
            if not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession):
                raise ValueError("TIMING_ACCESSION_INVALID")
            accessions.append(accession)
    if len(accessions) != 5 or len(set(accessions)) != 5:
        raise ValueError("TIMING_EXACT_FIVE_ACCESSIONS_REQUIRED")
    return targets


def checked_headers(status: int, headers: dict[str, str]) -> int:
    if status == 403:
        raise ValueError("TIMING_HTTP_403_STOP")
    if status == 429:
        raise ValueError("TIMING_HTTP_429_STOP")
    if status != 206:
        raise ValueError("TIMING_EXPECTED_206_STOP_NO_REDIRECT_OR_FULL_GET")
    match = re.fullmatch(r"bytes 0-([0-9]{1,10})/([0-9]{1,20})", headers.get("content-range", ""))
    if not match:
        raise ValueError("TIMING_CONTENT_RANGE_INVALID")
    end, total = int(match[1]), int(match[2])
    if total <= 0 or end != min(CAP, total) - 1:
        raise ValueError("TIMING_CONTENT_RANGE_OUTSIDE_APPROVED_PREFIX")
    length = headers.get("content-length", "")
    if not length.isdigit() or len(length) > 7 or int(length) != end + 1:
        raise ValueError("TIMING_CONTENT_LENGTH_MISMATCH")
    if (
        headers.get("transfer-encoding")
        or headers.get("content-encoding", "identity").lower() != "identity"
    ):
        raise ValueError("TIMING_REPRESENTATION_ENCODING_NOT_IDENTITY")
    mime = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if mime not in {
        "application/octet-stream",
        "binary/octet-stream",
        "application/gzip",
        "application/x-gzip",
        "application/x-tar",
        "application/x-compressed",
    }:
        raise ValueError("TIMING_ARCHIVE_CONTENT_TYPE_INVALID")
    return end + 1


class Ledger:
    def __init__(self, root: Path, binding: str, urls: list[str]) -> None:
        self.root, self.path = root, root / "state.json"
        root.mkdir(parents=True, exist_ok=True)
        if self.path.with_suffix(".pending").exists():
            raise ValueError("TIMING_INCOMPLETE_STATE_WRITE_STOP")
        if self.path.exists():
            self.state = json.loads(self.path.read_bytes())
            if self.state["binding"] != binding or self.state["urls"] != urls:
                raise ValueError("TIMING_NAMESPACE_IDENTITY_CHANGED")
            attempts = self.state["attempts"]
            if (
                len(attempts) > 4
                or len({a["url"] for a in attempts}) != len(attempts)
                or any(a["url"] not in urls for a in attempts)
                or self.state["received_body_bytes"]
                != sum(a["received_body_bytes"] for a in attempts)
            ):
                raise ValueError("TIMING_LEDGER_INVARIANT_FAILURE")
        else:
            if any(root.iterdir()):
                raise ValueError("TIMING_LEDGER_MISSING_IN_NONEMPTY_NAMESPACE")
            self.state = {
                "binding": binding,
                "urls": urls,
                "attempts": [],
                "received_body_bytes": 0,
                "status": "READY",
                "stop_reason": None,
            }
            self.save()

    def save(self) -> None:
        atomic_json(self.path, self.state)

    def reserve(self, url: str) -> None:
        if (
            self.state["status"] not in {"READY", "RUNNING"}
            or url not in self.state["urls"]
            or len(self.state["attempts"]) >= 4
            or any(a["url"] == url for a in self.state["attempts"])
        ):
            raise ValueError("TIMING_ATTEMPT_FORBIDDEN_NO_RESET_OR_RETRY")
        self.state["status"] = "RUNNING"
        self.state["attempts"].append(
            {
                "url": url,
                "reserved_at_utc": sec.utc_now().isoformat(),
                "received_body_bytes": 0,
                "http_status": None,
                "response_headers": {},
                "outcome": "RESERVED",
                "body_sha256": None,
                "object_locator": None,
            }
        )
        self.save()

    def response(self, status: int, headers: dict[str, str]) -> None:
        keep = {
            "content-range",
            "content-length",
            "content-type",
            "content-encoding",
            "transfer-encoding",
            "retry-after",
            "date",
            "etag",
            "last-modified",
            "location",
        }
        attempt = self.state["attempts"][-1]
        attempt["http_status"] = status
        attempt["response_headers"] = {k: v for k, v in headers.items() if k in keep}
        self.save()

    def charge(self, count: int) -> None:
        if type(count) is not int or count < 0:
            raise ValueError("TIMING_BYTE_COUNT_INVALID")
        attempt = self.state["attempts"][-1]
        attempt["received_body_bytes"] += count
        self.state["received_body_bytes"] += count
        if attempt["received_body_bytes"] > CAP or self.state["received_body_bytes"] > TOTAL:
            self.state["status"] = "STOPPED"
            self.state["stop_reason"] = "TIMING_BYTE_BUDGET_VIOLATION"
            self.save()
            raise ValueError("TIMING_BYTE_BUDGET_VIOLATION")
        self.save()

    def finish(self, body: bytes, outcome: str, proofs: list[dict[str, Any]]) -> None:
        attempt = self.state["attempts"][-1]
        if len(body) != attempt["received_body_bytes"]:
            raise ValueError("TIMING_RECEIVED_BYTES_BODY_MISMATCH")
        if body:
            cached = sec.Cache(self.root).store(attempt["url"], body, "application/octet-stream")
            attempt["body_sha256"] = cached["sha256"]
            attempt["object_locator"] = cached["object_locator"]
        attempt["outcome"] = outcome
        attempt["archive_proofs"] = proofs
        self.save()

    def stop(self, reason: str) -> None:
        self.state["status"], self.state["stop_reason"] = "STOPPED", reason
        self.save()


class Transport:
    def __init__(self, context: ssl.SSLContext, factory: Any = http.client.HTTPSConnection) -> None:
        self.context, self.factory = context, factory
        self.body = b""

    def get(self, target: dict[str, Any], agent: str, ledger: Ledger) -> bytes:
        self.body = b""
        parsed = urlsplit(target["url"])
        if (
            parsed.scheme != "https"
            or parsed.netloc != "www.sec.gov"
            or target["range"] != "bytes=0-1048575"
            or target["url"] not in ledger.state["urls"]
            or not ledger.state["attempts"]
            or ledger.state["attempts"][-1]["url"] != target["url"]
            or ledger.state["attempts"][-1]["outcome"] != "RESERVED"
        ):
            raise ValueError("TIMING_TRANSPORT_HOST_DENIED")
        token = sec.NETWORK_HOST.set("www.sec.gov")
        connection = None
        try:
            connection = self.factory("www.sec.gov", 443, timeout=30, context=self.context)
            connection.request(
                "GET",
                parsed.path,
                headers={
                    "User-Agent": agent,
                    "Range": target["range"],
                    "Accept-Encoding": "identity",
                    "Accept": "application/octet-stream,application/gzip",
                    "Connection": "close",
                },
            )
            reply = connection.getresponse()
            pairs = reply.getheaders()
            headers = {k.lower(): v for k, v in pairs}
            ledger.response(reply.status, headers)
            critical = {"content-range", "content-length", "content-encoding", "transfer-encoding"}
            names = [k.lower() for k, _ in pairs if k.lower() in critical]
            if len(names) != len(set(names)):
                raise ValueError("TIMING_DUPLICATE_FRAMING_HEADERS")
            remaining = checked_headers(reply.status, headers)
            while remaining:
                count = min(65536, remaining)
                try:
                    chunk = reply.read(count)
                except http.client.IncompleteRead as error:
                    self.body += error.partial
                    ledger.charge(len(error.partial))
                    raise ValueError("TIMING_PARTIAL_RESPONSE_STOP") from None
                if not chunk:
                    raise ValueError("TIMING_TRUNCATED_RESPONSE_STOP")
                self.body += chunk
                ledger.charge(len(chunk))
                if len(chunk) > count:
                    raise ValueError("TIMING_TRANSPORT_READ_BOUND_VIOLATION")
                remaining -= len(chunk)
            if not reply.isclosed():
                raise ValueError("TIMING_RESPONSE_END_UNVERIFIED_STOP")
            return self.body
        finally:
            if connection is not None:
                connection.close()
            sec.NETWORK_HOST.reset(token)


def prefix_proofs(body: bytes, accessions: list[str]) -> list[dict[str, Any]]:
    if not body.startswith(b"\x1f\x8b\x08"):
        raise ValueError("TIMING_GZIP_SIGNATURE_INVALID")
    return [
        archive.inspect_prefix([body], a, compressed_limit=CAP, decoded_limit=33_554_432)
        for a in accessions
    ]


def safe_reason(error: Exception) -> str:
    value = str(error)
    return (
        value
        if re.fullmatch(r"[A-Z][A-Z0-9_]{3,180}", value)
        else "TIMING_" + type(error).__name__.upper()
    )


def immutable(path: Path, value: Any) -> None:
    raw = canonical(value)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("TIMING_IMMUTABLE_OUTPUT_CONFLICT")
    else:
        sec.write_new(path, raw)


def preflight() -> tuple[dict[str, Any], list[dict[str, Any]], str, dict[str, Any]]:
    proof = sec.preflight()
    registry = json.loads((PROTOCOL / "sec-timing-diagnostics-extension-v1.json").read_bytes())
    if set(registry["files"]) != REGISTERED:
        raise ValueError("TIMING_REGISTRY_SCOPE_INVALID")
    for name, digest in registry["files"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("TIMING_REGISTERED_FILE_CHANGED")
    policy = json.loads((PROTOCOL / "sec-timing-diagnostics-policy-v1.json").read_bytes())
    for name, digest in policy["private_inputs"].items():
        path = (PRIVATE / name).resolve()
        if not path.is_relative_to(PRIVATE.resolve()) or sec.file_sha(path) != digest:
            raise ValueError("TIMING_PINNED_INPUT_CHANGED")
    plan = json.loads((PRIVATE / policy["proposal_file"]).read_bytes())
    authorization = json.loads((PRIVATE / policy["authorization_file"]).read_bytes())
    if (
        authorization["approved"] is not True
        or authorization["proposal_sha256"] != sec.sha(canonical(plan))
        or authorization["scope"] != "FOUR_SEC_TIMING_DIAGNOSTICS_ONLY"
    ):
        raise ValueError("TIMING_EXPLICIT_AUTHORIZATION_REQUIRED")
    verify_old_budgets(policy)
    old_archive = json.loads(
        (PROTOCOL / "offline-feed-prefix-verifier-extension-v1.json").read_bytes()
    )
    for name, digest in old_archive["files"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("TIMING_ARCHIVE_VERIFIER_CHANGED")
    proof["client_registry"] = registry
    proof["policy"] = policy
    proof["archive_registry"] = old_archive
    binding = sec.sha(canonical({"registry": registry, "authorization": authorization}))
    return plan, validate_plan(plan), binding, proof


def verify_old_budgets(policy: dict[str, Any]) -> None:
    if any(
        sec.file_sha(PRIVATE / name) != digest
        for name, digest in policy["old_budget_hashes"].items()
    ):
        raise ValueError("TIMING_PRIOR_BUDGET_MUTATION")
    old = json.loads((PRIVATE / policy["prior_global_attempt_counts_file"]).read_bytes())
    state_path = PRIVATE / "scheduler-state.json"
    current = (
        json.loads(state_path.read_bytes()).get("attempts_by_url", {})
        if state_path.exists()
        else {}
    )
    allowed = set(policy["approved_urls"])
    if (
        any(current.get(url) != count for url, count in old.items() if url not in allowed)
        or set(current) - set(old) - allowed
        or any(current.get(url, 0) not in {0, 1} for url in allowed)
    ):
        raise ValueError("TIMING_PRIOR_GLOBAL_ATTEMPT_BUDGET_CHANGED")


def audit(ledger: Ledger) -> dict[str, Any]:
    state = ledger.state
    if len(state["attempts"]) > 4 or state["received_body_bytes"] > TOTAL:
        raise ValueError("TIMING_FINAL_BUDGET_VIOLATION")
    located, seen = set(), 0
    for attempt in state["attempts"]:
        if attempt["received_body_bytes"] > CAP:
            raise ValueError("TIMING_FINAL_RESPONSE_BUDGET_VIOLATION")
        if attempt["received_body_bytes"]:
            body = sec.Cache(ledger.root).read(attempt["body_sha256"])
            if len(body) != attempt["received_body_bytes"]:
                raise ValueError("TIMING_CACHED_BYTE_COUNT_MISMATCH")
            for result in attempt["archive_proofs"]:
                checked = prefix_proofs(body, [result["accession"]])[0]
                if checked != result:
                    raise ValueError("TIMING_ARCHIVE_REPLAY_MISMATCH")
                if result["target_member"]:
                    located.add(result["accession"])
                seen += result["member_headers_seen"]
    return {
        "version": "sec-timing-diagnostics-v1",
        "status": state["status"],
        "stop_reason": state["stop_reason"],
        "request_attempts_reserved": len(state["attempts"]),
        "HTTP_responses_received": sum(a["http_status"] is not None for a in state["attempts"]),
        "received_body_bytes": state["received_body_bytes"],
        "request_cap": 4,
        "received_body_byte_cap": TOTAL,
        "automatic_retries": 0,
        "response_statuses": [a["http_status"] for a in state["attempts"]],
        "matched_target_accessions": sorted(located),
        "target_header_matches": len(located),
        "target_scans_member_headers_seen": seen,
        "public_times_certified": 0,
        "archive_day_semantics_certified": False,
        "whole_archive_fallbacks": 0,
        "prior_budgets_reset": 0,
        "unrelated_filing_bodies_extracted": 0,
        "original_718_sample_changes": 0,
        "market_data_accessed": False,
        "full_extraction_authorized": False,
        "final_qa_authorized": False,
        "phase3_authorized": False,
        "regressions_authorized": False,
        "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
    }


def run(command: str) -> dict[str, Any]:
    plan, targets, binding, proof = preflight()
    if command == "validate":
        contact = sec.private_contact(ROOT)
        del contact
        return {
            "status": "TIMING_OFFLINE_PREFLIGHT_PASS",
            "network_requests": 0,
            "exact_urls": len(targets),
            "target_accessions": 5,
            "byte_cap": TOTAL,
            "live_endpoint_validation": False,
        }
    agent = None
    context = None
    if command == "acquire":
        agent = (
            "CorporateEventAlphaResearch/timing-diagnostics-v1 (" + sec.private_contact(ROOT) + ")"
        )
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    sec.EXTENSION_FILES = (*sec.EXTENSION_FILES, *REGISTERED, *proof["archive_registry"]["files"])
    sec.install_firewall()
    if command == "audit":
        sys.addaudithook(
            lambda event, args: (
                (_ for _ in ()).throw(RuntimeError("TIMING_AUDIT_OFFLINE_ONLY"))
                if event.startswith(("socket.", "http.client."))
                else None
            )
        )
        if not (OUTPUT / "state.json").exists():
            raise ValueError("TIMING_AUDIT_REQUIRES_EXISTING_STATE")
        ledger = Ledger(OUTPUT, binding, [t["url"] for t in targets])
    else:
        assert context and agent
        with sec.Scheduler(PRIVATE) as scheduler:
            ledger = Ledger(OUTPUT, binding, [t["url"] for t in targets])
            if ledger.state["status"] == "RUNNING":
                ledger.stop("TIMING_PREVIOUS_INCOMPLETE_EXECUTION_NO_AUTOMATIC_RESUME")
            if ledger.state["status"] == "READY":
                transport = Transport(context)
                deadline = time.time() + 180
                for target in targets:
                    try:
                        if scheduler.attempts_used(target["url"]) != 0:
                            raise ValueError("TIMING_PRIOR_GLOBAL_URL_ATTEMPT_NO_RESET")
                        scheduler.start(deadline)
                        ledger.reserve(target["url"])
                        scheduler.record_attempt(target["url"])
                    except Exception as error:
                        ledger.stop(safe_reason(error))
                        break
                    print(
                        json.dumps(
                            {
                                "progress": "TIMING_REQUEST_RESERVED",
                                "ordinal": len(ledger.state["attempts"]),
                            }
                        ),
                        flush=True,
                    )
                    proofs = []
                    reason = None
                    try:
                        body = transport.get(target, agent, ledger)
                        proofs = prefix_proofs(body, target["target_accessions"])
                    except Exception as error:
                        reason = safe_reason(error)
                    ledger.finish(transport.body, reason or "PREFIX_INSPECTED", proofs)
                    if reason:
                        if reason == "TIMING_HTTP_403_STOP":
                            scheduler.block(reason)
                        elif reason == "TIMING_HTTP_429_STOP":
                            retry = ledger.state["attempts"][-1]["response_headers"].get(
                                "retry-after"
                            )
                            scheduler.hold(sec.retry_after_seconds(retry, sec.utc_now()), reason)
                            scheduler.block(reason)
                        ledger.stop(reason)
                        break
                else:
                    ledger.state["status"] = "COMPLETED_DIAGNOSTICS_NOT_CLOCK_CERTIFICATION"
                    ledger.save()
    verify_old_budgets(proof["policy"])
    summary = audit(ledger)
    immutable(OUTPUT / "summary.json", summary)
    immutable(
        OUTPUT / "completion-receipt.json",
        {
            "summary_sha256": sec.file_sha(OUTPUT / "summary.json"),
            "state_sha256": sec.file_sha(OUTPUT / "state.json"),
            "binding": binding,
            "client_registry": proof["client_registry"],
            "old_budget_hashes": proof["policy"]["old_budget_hashes"],
        },
    )
    return summary


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("command", choices=["validate", "acquire", "audit"])
    try:
        print(json.dumps(run(cli.parse_args().command)), flush=True)
    except Exception as error:
        print(json.dumps({"status": "TIMING_STOPPED", "reason": safe_reason(error)}), flush=True)
        raise SystemExit(2) from None
