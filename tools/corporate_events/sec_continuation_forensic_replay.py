"""Replay safely retained bytes after an incomplete continuation ledger write, offline only."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sec_partial_feed_scan as scanner  # noqa: E402
import sec_partial_feed_search as previous  # noqa: E402
import sec_stopped_feed_continuation as continuation  # noqa: E402
import sec_timing_diagnostics as base  # noqa: E402
import sec_two_archive_content_check as content  # noqa: E402
import sec_two_archive_ranges as ranges  # noqa: E402

sec = base.sec
ROOT, PRIVATE, PROTOCOL = continuation.ROOT, continuation.PRIVATE, continuation.PROTOCOL
OUTPUT = PRIVATE / "continuation-forensic-replay-v1"
REGISTERED = {
    "tools/corporate_events/sec_continuation_forensic_replay.py",
    "tests/test_sec_continuation_forensic_replay.py",
    "protocols/corporate-event-alpha-v1/sec-continuation-forensic-replay-policy-v1.json",
}


def validate_states(
    durable: dict[str, Any], pending: dict[str, Any], saved_bytes: int
) -> dict[str, int]:
    if (
        durable.get("status") != "RUNNING"
        or pending.get("status") != "RUNNING"
        or len(durable.get("attempts", [])) != 1
        or len(pending.get("attempts", [])) != 1
    ):
        raise ValueError("FORENSIC_UNEXPECTED_LEDGER_STATE")
    old, new = durable["attempts"][0], pending["attempts"][0]
    if (
        old.get("url") != continuation.URL
        or (old.get("start"), old.get("end")) != (continuation.START, continuation.END)
        or old.get("status") != 206
        or old.get("outcome") != "RESERVED"
        or saved_bytes != old.get("bytes")
        or durable.get("received_body_bytes") != old.get("bytes")
        or pending.get("received_body_bytes") != new.get("bytes")
    ):
        raise ValueError("FORENSIC_DURABLE_BYTES_OR_ATTEMPT_INVALID")
    low, high = durable["received_body_bytes"], pending["received_body_bytes"]
    if (
        type(low) is not int
        or type(high) is not int
        or not 0 < low < high <= continuation.CAP
        or high - low > 1_048_576
    ):
        raise ValueError("FORENSIC_PENDING_HIGH_WATER_INVALID")
    for key in set(durable) | set(pending):
        if key not in {"attempts", "received_body_bytes"} and durable.get(key) != pending.get(key):
            raise ValueError("FORENSIC_LEDGER_FIELDS_DIFFER")
    for key in set(old) | set(new):
        if key != "bytes" and old.get(key) != new.get(key):
            raise ValueError("FORENSIC_ATTEMPT_FIELDS_DIFFER")
    return {
        "durable_received_bytes": low,
        "pending_received_high_water_bytes": high,
        "bytes_not_preserved_in_fragment": high - low,
    }


def run() -> dict[str, Any]:
    target, proof, binding = continuation.preflight()
    registry = json.loads(
        (PROTOCOL / "sec-continuation-forensic-replay-extension-v1.json").read_bytes()
    )
    if set(registry["files"]) != REGISTERED:
        raise ValueError("FORENSIC_REGISTRY_SCOPE_INVALID")
    for name, digest in registry["files"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("FORENSIC_REGISTERED_FILE_CHANGED")
    policy = json.loads((PROTOCOL / "sec-continuation-forensic-replay-policy-v1.json").read_bytes())
    for name, digest in policy["private_inputs"].items():
        if ranges.sha_file(PRIVATE / name) != digest:
            raise ValueError("FORENSIC_PINNED_INPUT_CHANGED")
    for name, digest in policy["dependencies"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("FORENSIC_DEPENDENCY_CHANGED")
    auth = json.loads((PRIVATE / "stopped-feed-continuation-authorization-v1.json").read_bytes())
    if (
        auth.get("approved") is not True
        or auth.get("registry_binding") != binding
        or auth.get("scope") != "ONE_2011_RANGE_AFTER_STOP_ONLY"
    ):
        raise ValueError("FORENSIC_ORIGINAL_APPROVAL_INVALID")
    original = continuation.OUTPUT
    durable = json.loads((original / "state.json").read_bytes())
    pending = json.loads((original / "state.pending").read_bytes())
    expected_binding = sec.sha(base.canonical({"registry": binding, "approval": auth}))
    if durable["binding"] != expected_binding:
        raise ValueError("FORENSIC_LEDGER_BINDING_CHANGED")
    fragment = original / "range-01.part"
    water = validate_states(durable, pending, fragment.stat().st_size)
    attempt = durable["attempts"][0]
    ranges.checked_headers(
        attempt["status"],
        list(attempt["headers"].items()),
        target,
        continuation.START,
        continuation.END,
    )
    scheduler = json.loads((PRIVATE / "scheduler-state.json").read_bytes())["attempts_by_url"]
    if scheduler[continuation.URL] != 5:
        raise ValueError("FORENSIC_GLOBAL_ATTEMPT_COUNT_INVALID")
    old_state = json.loads((previous.OUTPUT / "state.json").read_bytes())
    paths = continuation.old_fragments(target, old_state) + [fragment]
    source_info = next(f for f in target["filings"] if f["accession"] == continuation.ACCESSION)
    source = sec.Cache(PRIVATE / "repurchase-acquisition-v1").read(source_info["source_sha256"])
    sec.EXTENSION_FILES = (
        *sec.EXTENSION_FILES,
        *REGISTERED,
        *proof["policy"]["dependencies"],
        *policy["dependencies"],
    )
    sec.install_firewall()
    sys.addaudithook(
        lambda event, args: (
            (_ for _ in ()).throw(RuntimeError("FORENSIC_REPLAY_NETWORK_DENIED"))
            if event.startswith(("socket.", "http.client."))
            else None
        )
    )
    scanned = scanner.scan(paths, {continuation.ACCESSION: source}, content.compare)
    result = {
        "version": "continuation-forensic-replay-v1",
        "status": "OFFLINE_FORENSIC_REPLAY_COMPLETE",
        "original_incomplete_state_preserved": True,
        "original_exception": "FILEEXISTSERROR",
        "underlying_state_write_cause": "NOT_RECORDED",
        "prior_continuation_attempts": 1,
        "global_URL_attempts": 5,
        **water,
        "saved_fragment_sha256": ranges.sha_file(fragment),
        "scan": scanned,
        "new_HTTP_requests": 0,
        "prior_budgets_reset": 0,
        "public_times_certified": 0,
        "regressions_run": 0,
        "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    base.immutable(OUTPUT / "summary.json", result)
    base.immutable(
        OUTPUT / "receipt.json",
        {
            "summary_sha256": sec.file_sha(OUTPUT / "summary.json"),
            "registry": registry,
            "private_inputs": policy["private_inputs"],
        },
    )
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(run()), flush=True)
    except Exception as error:
        print(json.dumps({"status": "FORENSIC_REPLAY_STOPPED", "reason": base.safe_reason(error)}))
        raise SystemExit(2) from None
