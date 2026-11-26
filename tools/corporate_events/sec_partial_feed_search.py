"""Search two SEC archive prefixes under the original per-URL and response limits."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import shutil
import ssl
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sec_partial_feed_scan as scanner  # noqa: E402
import sec_timing_diagnostics as base  # noqa: E402
import sec_two_archive_content_check as content  # noqa: E402
import sec_two_archive_ranges as ranges  # noqa: E402

sec = base.sec
ROOT, PRIVATE, PROTOCOL = base.ROOT, base.PRIVATE, base.PROTOCOL
OUTPUT = PRIVATE / "partial-feed-search-v1"
URLS = [
    "https://www.sec.gov/Archives/edgar/Feed/2010/QTR3/20100928.nc.tar.gz",
    "https://www.sec.gov/Archives/edgar/Feed/2011/QTR4/20111130.nc.tar.gz",
]
SIZES = [281_193_698, 212_408_366]
ACCESSIONS = [
    ["0000950123-10-089334"],
    ["0001193125-11-325829", "0001206774-11-002703"],
]
CAP = 419_430_400
REGISTERED = {
    "tools/corporate_events/sec_partial_feed_search.py",
    "tests/test_sec_partial_feed_search.py",
    "protocols/corporate-event-alpha-v1/sec-partial-feed-search-policy-v1.json",
}


def validate_targets(targets: list[dict[str, Any]]) -> None:
    if len(targets) != 2:
        raise ValueError("PARTIAL_SEARCH_TARGET_COUNT_INVALID")
    for i, target in enumerate(targets):
        if (
            target["url"] != URLS[i]
            or target["total_bytes"] != SIZES[i]
            or [f["accession"] for f in target["filings"]] != ACCESSIONS[i]
            or not re.fullmatch(r'"[A-Za-z0-9-]+"', target["etag"])
            or not re.fullmatch(r"[0-9a-f]{64}", target["prefix_sha256"])
            or any(not re.fullmatch(r"[0-9a-f]{64}", f["source_sha256"]) for f in target["filings"])
        ):
            raise ValueError("PARTIAL_SEARCH_TARGET_IDENTITY_INVALID")


class Ledger:
    def __init__(self, root: Path, binding: str) -> None:
        self.root, self.path = root, root / "state.json"
        root.mkdir(parents=True, exist_ok=True)
        if self.path.with_suffix(".pending").exists():
            raise ValueError("PARTIAL_SEARCH_INCOMPLETE_STATE_WRITE")
        if self.path.exists():
            self.state = json.loads(self.path.read_bytes())
            if self.state["binding"] != binding:
                raise ValueError("PARTIAL_SEARCH_BINDING_CHANGED")
        else:
            if any(root.iterdir()):
                raise ValueError("PARTIAL_SEARCH_LEDGER_MISSING")
            self.state = {
                "binding": binding,
                "status": "READY",
                "stop_reason": None,
                "attempts": [],
                "received_body_bytes": 0,
                "searches": [],
            }
            self.save()
        attempts = self.state["attempts"]
        if (
            len(attempts) > 8
            or self.state["received_body_bytes"] > CAP
            or self.state["received_body_bytes"] != sum(a["bytes"] for a in attempts)
            or any(
                a["url"] not in URLS or not 0 <= a["bytes"] <= ranges.RANGE_CAP for a in attempts
            )
            or any(sum(a["url"] == url for a in attempts) > 4 for url in URLS)
            or len({(a["url"], a["start"], a["end"]) for a in attempts}) != len(attempts)
        ):
            raise ValueError("PARTIAL_SEARCH_LEDGER_INVARIANT_FAILURE")

    def save(self) -> None:
        base.atomic_json(self.path, self.state)

    def reserve(self, url: str, start: int, end: int) -> None:
        prior = [a for a in self.state["attempts"] if a["url"] == url]
        if (
            self.state["status"] not in {"READY", "RUNNING"}
            or url not in URLS
            or len(self.state["attempts"]) >= 8
            or len(prior) >= 4
            or (start, end) != ranges.ranges(SIZES[URLS.index(url)])[len(prior)]
            or any(a["outcome"] != "RANGE_CACHED" for a in prior)
        ):
            raise ValueError("PARTIAL_SEARCH_ATTEMPT_FORBIDDEN")
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
            or attempt["bytes"] + count > attempt["end"] - attempt["start"] + 1
            or attempt["bytes"] + count > ranges.RANGE_CAP
            or self.state["received_body_bytes"] + count > CAP
        ):
            self.stop("PARTIAL_SEARCH_BYTE_CAP_VIOLATION")
            raise ValueError("PARTIAL_SEARCH_BYTE_CAP_VIOLATION")
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
        or target["url"] not in URLS
        or attempt["outcome"] != "RESERVED"
        or attempt["status"] is not None
        or (attempt["start"], attempt["end"]) != (start, end)
        or not context.check_hostname
        or context.verify_mode != ssl.CERT_REQUIRED
    ):
        raise ValueError("PARTIAL_SEARCH_UNRESERVED_OR_UNVERIFIED_TRANSPORT")
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
        ranges.checked_headers(response.status, pairs, target, start, end)
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
                    raise ValueError("PARTIAL_SEARCH_PARTIAL_RESPONSE_STOP") from None
                ledger.charge(len(block))
                out.write(block)
                if not block or len(block) > count:
                    raise ValueError("PARTIAL_SEARCH_TRUNCATED_OR_OVERSIZE_READ")
                remaining -= len(block)
            out.flush()
            os.fsync(out.fileno())
        if not response.isclosed():
            raise ValueError("PARTIAL_SEARCH_RESPONSE_END_UNVERIFIED")
        digest = ranges.sha_file(path)
        obj = ledger.root / "objects" / "sha256" / digest[:2] / digest
        obj.parent.mkdir(parents=True, exist_ok=True)
        if obj.exists():
            if ranges.sha_file(obj) != digest:
                raise ValueError("PARTIAL_SEARCH_CACHE_CONFLICT")
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


def scan_target(target: dict[str, Any], ledger: Ledger) -> dict[str, Any]:
    prefix = sec.Cache(PRIVATE / "repurchase-timing-diagnostics-v1").object_path(
        target["prefix_sha256"]
    )
    if ranges.sha_file(prefix) != target["prefix_sha256"] or prefix.stat().st_size != ranges.PREFIX:
        raise ValueError("PARTIAL_SEARCH_PREFIX_CHANGED")
    selected = [
        a
        for a in ledger.state["attempts"]
        if a["url"] == target["url"] and a["outcome"] == "RANGE_CACHED"
    ]
    if [(a["start"], a["end"]) for a in selected] != ranges.ranges(target["total_bytes"])[
        : len(selected)
    ]:
        raise ValueError("PARTIAL_SEARCH_RANGE_COVERAGE_INVALID")
    paths = [prefix]
    for attempt in selected:
        path = ledger.root / attempt["locator"]
        if ranges.sha_file(path) != attempt["sha256"] or path.stat().st_size != attempt["bytes"]:
            raise ValueError("PARTIAL_SEARCH_CACHED_RANGE_CHANGED")
        paths.append(path)
    expected = {
        f["accession"]: sec.Cache(PRIVATE / "repurchase-acquisition-v1").read(f["source_sha256"])
        for f in target["filings"]
    }
    return scanner.scan(paths, expected, content.compare)


def verify_prior(plan: dict[str, Any]) -> None:
    prior = json.loads((PRIVATE / "partial-feed-search-prior-attempts-v1.json").read_bytes())
    current = json.loads((PRIVATE / "scheduler-state.json").read_bytes())["attempts_by_url"]
    if (
        set(current) != set(prior)
        or any(current[k] != n for k, n in prior.items() if k not in URLS)
        or any(prior.get(url) != 1 or current[url] not in range(1, 6) for url in URLS)
    ):
        raise ValueError("PARTIAL_SEARCH_PRIOR_ATTEMPTS_CHANGED")


def preflight() -> tuple[dict[str, Any], str]:
    sec.preflight()
    registry = json.loads((PROTOCOL / "sec-partial-feed-search-extension-v1.json").read_bytes())
    if set(registry["files"]) != REGISTERED:
        raise ValueError("PARTIAL_SEARCH_REGISTRY_SCOPE_INVALID")
    for name, digest in registry["files"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("PARTIAL_SEARCH_REGISTERED_FILE_CHANGED")
    policy = json.loads((PROTOCOL / "sec-partial-feed-search-policy-v1.json").read_bytes())
    for name, digest in policy["private_inputs"].items():
        if sec.file_sha(PRIVATE / name) != digest:
            raise ValueError("PARTIAL_SEARCH_PINNED_INPUT_CHANGED")
    for name, digest in policy["dependencies"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("PARTIAL_SEARCH_DEPENDENCY_CHANGED")
    plan: dict[str, Any] = json.loads((PRIVATE / "partial-feed-search-plan-v1.json").read_bytes())
    validate_targets(plan["targets"])
    verify_prior(plan)
    plan["policy"], plan["registry"] = policy, registry
    return plan, sec.sha(base.canonical(registry))


def run(command: str) -> dict[str, Any]:
    plan, binding = preflight()
    if command == "validate":
        contact = sec.private_contact(ROOT)
        del contact
        if shutil.disk_usage(PRIVATE).free < 700_000_000:
            raise ValueError("PARTIAL_SEARCH_INSUFFICIENT_DISK_SPACE")
        return {
            "status": "OFFLINE_PREFLIGHT_PASS",
            "request_cap": 8,
            "new_body_byte_cap": CAP,
            "contact_configured": True,
            "network_requests": 0,
            "registry_binding": binding,
        }
    approval = json.loads((PRIVATE / "partial-feed-search-authorization-v1.json").read_bytes())
    if (
        approval.get("approved") is not True
        or approval.get("registry_binding") != binding
        or approval.get("scope") != "TWO_PARTIAL_ARCHIVES_THREE_FILINGS_ONLY"
    ):
        raise ValueError("PARTIAL_SEARCH_AUTHORIZATION_REQUIRED")
    ledger_binding = sec.sha(base.canonical({"registry": binding, "approval": approval}))
    context, agent = None, None
    if command == "acquire":
        agent = (
            "CorporateEventAlphaResearch/partial-feed-search-v1 (" + sec.private_contact(ROOT) + ")"
        )
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    sec.EXTENSION_FILES = (*sec.EXTENSION_FILES, *REGISTERED, *plan["policy"]["dependencies"])
    sec.install_firewall()
    if command == "audit":
        sys.addaudithook(
            lambda event, args: (
                (_ for _ in ()).throw(RuntimeError("PARTIAL_SEARCH_AUDIT_OFFLINE_ONLY"))
                if event.startswith(("socket.", "http.client."))
                else None
            )
        )
        if not (OUTPUT / "state.json").exists():
            raise ValueError("PARTIAL_SEARCH_AUDIT_REQUIRES_LEDGER")
        ledger = Ledger(OUTPUT, ledger_binding)
    else:
        assert context and agent
        with sec.Scheduler(PRIVATE) as scheduler:
            ledger = Ledger(OUTPUT, ledger_binding)
            if ledger.state["status"] == "RUNNING":
                ledger.stop("PARTIAL_SEARCH_INTERRUPTED_NO_AUTOMATIC_RESUME")
            if ledger.state["status"] == "READY":
                try:
                    for target in plan["targets"]:
                        proof = scan_target(target, ledger)
                        if proof["matches"]:
                            raise ValueError("PARTIAL_SEARCH_BASELINE_MUST_BE_PREVIOUSLY_EMPTY")
                        for start, end in ranges.ranges(target["total_bytes"])[:4]:
                            expected = 1 + sum(
                                a["url"] == target["url"] for a in ledger.state["attempts"]
                            )
                            if scheduler.attempts_used(target["url"]) != expected or expected >= 5:
                                raise ValueError("PARTIAL_SEARCH_GLOBAL_ATTEMPT_LIMIT")
                            scheduler.start(time.time() + 1800)
                            ledger.reserve(target["url"], start, end)
                            scheduler.record_attempt(target["url"])
                            receive(target, start, end, agent, ledger, context)
                            proof = scan_target(target, ledger)
                            ledger.state["attempts"][-1]["scan"] = proof
                            ledger.save()
                            print(
                                json.dumps(
                                    {
                                        "range_completed": len(ledger.state["attempts"]),
                                        "received_body_bytes": ledger.state["received_body_bytes"],
                                        "target_members_found_in_prefix": len(proof["matches"]),
                                    }
                                ),
                                flush=True,
                            )
                            if not proof["unlocated_accessions"]:
                                break
                        ledger.state["searches"].append({"url": target["url"], "proof": proof})
                        ledger.save()
                    ledger.state["status"] = "COMPLETED_PARTIAL_MEMBERSHIP_NOT_CLOCK_CERTIFICATION"
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
                    ledger.stop(reason)
    verify_prior(plan)
    for attempt in ledger.state["attempts"]:
        if attempt["outcome"] == "RANGE_CACHED":
            path = OUTPUT / attempt["locator"]
            if (
                ranges.sha_file(path) != attempt["sha256"]
                or path.stat().st_size != attempt["bytes"]
            ):
                raise ValueError("PARTIAL_SEARCH_CACHE_REPLAY_FAILURE")
    if command == "audit":
        for search in ledger.state["searches"]:
            target = next(t for t in plan["targets"] if t["url"] == search["url"])
            if scan_target(target, ledger) != search["proof"]:
                raise ValueError("PARTIAL_SEARCH_OFFLINE_PROOF_MISMATCH")
    located = {m["accession"] for s in ledger.state["searches"] for m in s["proof"]["matches"]}
    located.update(
        m["accession"]
        for a in ledger.state["attempts"]
        for m in a.get("scan", {}).get("matches", [])
    )
    summary = {
        "status": ledger.state["status"],
        "stop_reason": ledger.state["stop_reason"],
        "HTTP_attempts": len(ledger.state["attempts"]),
        "received_body_bytes": ledger.state["received_body_bytes"],
        "HTTP_statuses": [a["status"] for a in ledger.state["attempts"]],
        "target_accessions_located": sorted(located),
        "target_members_found": len(located),
        "gzip_eof_and_checksum_verified": False,
        "prefix_miss_proves_absence": False,
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
    cli.add_argument("command", choices=["validate", "acquire", "audit"])
    try:
        print(json.dumps(run(cli.parse_args().command)), flush=True)
    except Exception as error:
        print(json.dumps({"status": "PARTIAL_SEARCH_STOPPED", "reason": base.safe_reason(error)}))
        raise SystemExit(2) from None
