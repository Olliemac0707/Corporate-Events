"""One separately authorized range following a sealed interrupted SEC archive response."""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sec_partial_feed_scan as scanner  # noqa: E402
import sec_partial_feed_search as previous  # noqa: E402
import sec_timing_diagnostics as base  # noqa: E402
import sec_two_archive_content_check as content  # noqa: E402
import sec_two_archive_ranges as ranges  # noqa: E402

sec = base.sec
ROOT, PRIVATE, PROTOCOL = previous.ROOT, previous.PRIVATE, previous.PROTOCOL
OUTPUT = PRIVATE / "stopped-feed-continuation-v1"
URL = previous.URLS[1]
ACCESSION = "0001206774-11-002703"
START, END, CAP = 155_189_248, 207_618_047, 52_428_800
REGISTERED = {
    "tools/corporate_events/sec_stopped_feed_continuation.py",
    "tests/test_sec_stopped_feed_continuation.py",
    "protocols/corporate-event-alpha-v1/sec-stopped-feed-continuation-policy-v1.json",
}


class Ledger:
    def __init__(self, root: Path, binding: str) -> None:
        self.root, self.path = root, root / "state.json"
        root.mkdir(parents=True, exist_ok=True)
        if self.path.with_suffix(".pending").exists():
            raise ValueError("CONTINUATION_INCOMPLETE_STATE_WRITE")
        if self.path.exists():
            self.state = json.loads(self.path.read_bytes())
            if self.state["binding"] != binding:
                raise ValueError("CONTINUATION_BINDING_CHANGED")
        else:
            if any(root.iterdir()):
                raise ValueError("CONTINUATION_LEDGER_MISSING")
            self.state = {
                "binding": binding,
                "status": "READY",
                "stop_reason": None,
                "attempts": [],
                "received_body_bytes": 0,
            }
            self.save()
        if (
            len(self.state["attempts"]) > 1
            or self.state["received_body_bytes"] > CAP
            or self.state["received_body_bytes"] != sum(a["bytes"] for a in self.state["attempts"])
            or any(
                a["url"] != URL
                or (a["start"], a["end"]) != (START, END)
                or not 0 <= a["bytes"] <= CAP
                for a in self.state["attempts"]
            )
        ):
            raise ValueError("CONTINUATION_LEDGER_INVARIANT_FAILURE")

    def save(self) -> None:
        base.atomic_json(self.path, self.state)

    def reserve(self) -> None:
        if self.state["status"] != "READY" or self.state["attempts"]:
            raise ValueError("CONTINUATION_ATTEMPT_FORBIDDEN")
        self.state["status"] = "RUNNING"
        self.state["attempts"].append(
            {
                "url": URL,
                "start": START,
                "end": END,
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
        if type(count) is not int or count < 0 or self.state["received_body_bytes"] + count > CAP:
            self.stop("CONTINUATION_BYTE_CAP_VIOLATION")
            raise ValueError("CONTINUATION_BYTE_CAP_VIOLATION")
        self.state["attempts"][-1]["bytes"] += count
        self.state["received_body_bytes"] += count
        self.save()

    def stop(self, reason: str) -> None:
        self.state.update(status="STOPPED", stop_reason=reason)
        self.save()


def old_fragments(target: dict[str, Any], state: dict[str, Any]) -> list[Path]:
    if (
        state["status"] != "STOPPED"
        or state["stop_reason"] != "PARTIAL_SEARCH_TRUNCATED_OR_OVERSIZE_READ"
    ):
        raise ValueError("CONTINUATION_PRIOR_STOP_NOT_PRESERVED")
    attempts = [a for a in state["attempts"] if a["url"] == URL]
    if len(attempts) != 3:
        raise ValueError("CONTINUATION_PRIOR_ATTEMPTS_INVALID")
    prefix = sec.Cache(PRIVATE / "repurchase-timing-diagnostics-v1").object_path(
        target["prefix_sha256"]
    )
    if prefix.stat().st_size != ranges.PREFIX or ranges.sha_file(prefix) != target["prefix_sha256"]:
        raise ValueError("CONTINUATION_PREFIX_CHANGED")
    paths = [prefix]
    for index, (attempt, span) in enumerate(
        zip(attempts, ranges.ranges(target["total_bytes"])[:3], strict=True)
    ):
        if (attempt["start"], attempt["end"]) != span or attempt["status"] != 206:
            raise ValueError("CONTINUATION_PRIOR_RANGE_INVALID")
        ranges.checked_headers(attempt["status"], list(attempt["headers"].items()), target, *span)
        if index < 2:
            if attempt["outcome"] != "RANGE_CACHED" or attempt["bytes"] != ranges.RANGE_CAP:
                raise ValueError("CONTINUATION_PRIOR_COMPLETE_RANGE_INVALID")
            path, digest = previous.OUTPUT / attempt["locator"], attempt["sha256"]
        else:
            if (
                attempt["outcome"] != "PARTIAL_SEARCH_TRUNCATED_OR_OVERSIZE_READ"
                or attempt["start"] + attempt["bytes"] != START
            ):
                raise ValueError("CONTINUATION_PRIOR_FRAGMENT_INVALID")
            path, digest = previous.OUTPUT / attempt["partial_locator"], attempt["partial_sha256"]
        if path.stat().st_size != attempt["bytes"] or ranges.sha_file(path) != digest:
            raise ValueError("CONTINUATION_PRIOR_FRAGMENT_CHANGED")
        paths.append(path)
    return paths


def verify_prior() -> None:
    snapshot = json.loads(
        (PRIVATE / "stopped-feed-continuation-prior-attempts-v1.json").read_bytes()
    )
    current = json.loads((PRIVATE / "scheduler-state.json").read_bytes())["attempts_by_url"]
    if (
        set(current) != set(snapshot)
        or any(current[k] != value for k, value in snapshot.items() if k != URL)
        or snapshot.get(URL) != 4
        or current[URL] not in {4, 5}
    ):
        raise ValueError("CONTINUATION_PRIOR_ATTEMPTS_CHANGED")


def preflight() -> tuple[dict[str, Any], dict[str, Any], str]:
    plan, _ = previous.preflight()
    registry = json.loads(
        (PROTOCOL / "sec-stopped-feed-continuation-extension-v1.json").read_bytes()
    )
    if set(registry["files"]) != REGISTERED:
        raise ValueError("CONTINUATION_REGISTRY_SCOPE_INVALID")
    for name, digest in registry["files"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("CONTINUATION_REGISTERED_FILE_CHANGED")
    policy = json.loads((PROTOCOL / "sec-stopped-feed-continuation-policy-v1.json").read_bytes())
    for name, digest in policy["private_inputs"].items():
        if ranges.sha_file(PRIVATE / name) != digest:
            raise ValueError("CONTINUATION_PINNED_INPUT_CHANGED")
    for name, digest in policy["dependencies"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("CONTINUATION_DEPENDENCY_CHANGED")
    if (
        policy["url"] != URL
        or policy["target_accession"] != ACCESSION
        or policy["range_start"] != START
        or policy["range_end"] != END
        or policy["byte_cap"] != CAP
    ):
        raise ValueError("CONTINUATION_SCOPE_CHANGED")
    target = plan["targets"][1]
    if target["url"] != URL or ACCESSION not in [f["accession"] for f in target["filings"]]:
        raise ValueError("CONTINUATION_TARGET_CHANGED")
    state = json.loads((previous.OUTPUT / "state.json").read_bytes())
    old_fragments(target, state)
    replay = json.loads((PRIVATE / "partial-feed-interrupted-replay-v1/summary.json").read_bytes())
    if (
        replay["original_stop_preserved"] is not True
        or replay["result"]["unlocated_accessions"] != [ACCESSION]
        or replay["public_times_certified"] != 0
    ):
        raise ValueError("CONTINUATION_PRIOR_REPLAY_INVALID")
    verify_prior()
    return target, {"registry": registry, "policy": policy}, sec.sha(base.canonical(registry))


def run(command: str) -> dict[str, Any]:
    target, proof, binding = preflight()
    if command == "validate":
        contact = sec.private_contact(ROOT)
        del contact
        return {
            "status": "OFFLINE_READY_AWAITING_EXPLICIT_CONTINUATION",
            "request_cap": 1,
            "new_body_byte_cap": CAP,
            "contact_configured": True,
            "network_requests": 0,
            "registry_binding": binding,
            "already_cached_fragment_reused": True,
        }
    auth_path = PRIVATE / "stopped-feed-continuation-authorization-v1.json"
    if not auth_path.exists():
        raise ValueError("CONTINUATION_EXPLICIT_APPROVAL_REQUIRED")
    approval = json.loads(auth_path.read_bytes())
    if (
        approval.get("approved") is not True
        or approval.get("registry_binding") != binding
        or approval.get("scope") != "ONE_2011_RANGE_AFTER_STOP_ONLY"
        or not approval.get("verbatim_user_approval")
    ):
        raise ValueError("CONTINUATION_EXPLICIT_APPROVAL_REQUIRED")
    ledger_binding = sec.sha(base.canonical({"registry": binding, "approval": approval}))
    context, agent = None, None
    if command == "acquire":
        agent = (
            "CorporateEventAlphaResearch/stopped-feed-continuation-v1 ("
            + sec.private_contact(ROOT)
            + ")"
        )
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    sec.EXTENSION_FILES = (*sec.EXTENSION_FILES, *REGISTERED, *proof["policy"]["dependencies"])
    sec.install_firewall()
    if command == "audit":
        sys.addaudithook(
            lambda event, args: (
                (_ for _ in ()).throw(RuntimeError("CONTINUATION_AUDIT_OFFLINE_ONLY"))
                if event.startswith(("socket.", "http.client."))
                else None
            )
        )
        if not (OUTPUT / "state.json").exists():
            raise ValueError("CONTINUATION_AUDIT_REQUIRES_LEDGER")
        ledger = Ledger(OUTPUT, ledger_binding)
    else:
        assert context and agent
        with sec.Scheduler(PRIVATE) as scheduler:
            ledger = Ledger(OUTPUT, ledger_binding)
            if ledger.state["status"] == "RUNNING":
                ledger.stop("CONTINUATION_INTERRUPTED_NO_AUTOMATIC_RESUME")
            if ledger.state["status"] == "READY":
                try:
                    if scheduler.attempts_used(URL) != 4:
                        raise ValueError("CONTINUATION_GLOBAL_ATTEMPT_LIMIT")
                    scheduler.start(time.time() + 1800)
                    ledger.reserve()
                    scheduler.record_attempt(URL)
                    ledger_for_transport: Any = ledger
                    previous.receive(target, START, END, agent, ledger_for_transport, context)
                    ledger.state["status"] = "RANGE_COMPLETE_PENDING_OFFLINE_SCAN"
                    ledger.save()
                except Exception as error:
                    reason = base.safe_reason(error)
                    if (
                        ledger.state["attempts"]
                        and ledger.state["attempts"][-1]["outcome"] == "RESERVED"
                    ):
                        attempt = ledger.state["attempts"][-1]
                        attempt["outcome"] = reason
                        fragment = OUTPUT / "range-01.part"
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
    verify_prior()
    attempts = ledger.state["attempts"]
    scan = None
    if attempts and attempts[0]["outcome"] == "RANGE_CACHED":
        path = OUTPUT / attempts[0]["locator"]
        if (
            ranges.sha_file(path) != attempts[0]["sha256"]
            or path.stat().st_size != attempts[0]["bytes"]
        ):
            raise ValueError("CONTINUATION_CACHE_REPLAY_FAILURE")
        paths = old_fragments(target, json.loads((previous.OUTPUT / "state.json").read_bytes())) + [
            path
        ]
        source_info = next(f for f in target["filings"] if f["accession"] == ACCESSION)
        source = sec.Cache(PRIVATE / "repurchase-acquisition-v1").read(source_info["source_sha256"])
        scan = scanner.scan(paths, {ACCESSION: source}, content.compare)
        if command == "acquire":
            ledger.state["status"] = "COMPLETED_PARTIAL_MEMBERSHIP_NOT_CLOCK_CERTIFICATION"
            ledger.save()
    result = {
        "status": ledger.state["status"],
        "stop_reason": ledger.state["stop_reason"],
        "HTTP_attempts": len(attempts),
        "received_body_bytes": ledger.state["received_body_bytes"],
        "scan": scan,
        "gzip_eof_and_checksum_verified": False,
        "prefix_miss_proves_absence": False,
        "public_times_certified": 0,
        "prior_budgets_reset": 0,
        "unrelated_filing_bodies_extracted": 0,
        "regressions_run": 0,
        "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
    }
    base.immutable(OUTPUT / "summary.json", result)
    base.immutable(
        OUTPUT / "completion-receipt.json",
        {
            "state_sha256": sec.file_sha(OUTPUT / "state.json"),
            "summary_sha256": sec.file_sha(OUTPUT / "summary.json"),
            "binding": ledger_binding,
            "registry": proof["registry"],
        },
    )
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("command", choices=["validate", "acquire", "audit"])
    try:
        print(json.dumps(run(cli.parse_args().command)), flush=True)
    except Exception as error:
        print(json.dumps({"status": "CONTINUATION_STOPPED", "reason": base.safe_reason(error)}))
        raise SystemExit(2) from None
