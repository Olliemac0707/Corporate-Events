"""Inventory five source-linked timing records without assigning public clocks."""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sec_timing_diagnostics as base  # noqa: E402
import sec_two_archive_ranges as ranges  # noqa: E402

sec = base.sec
ROOT, PRIVATE, PROTOCOL = base.ROOT, base.PRIVATE, base.PROTOCOL
OUTPUT = PRIVATE / "repurchase-five-clock-inventory-v1"
ACCESSIONS = [
    "0000902664-07-002404",
    "0001104659-08-048595",
    "0000950123-10-089334",
    "0001193125-11-325829",
    "0001206774-11-002703",
]
REGISTERED = {
    "tools/corporate_events/repurchase_five_clock_inventory.py",
    "tests/test_repurchase_five_clock_inventory.py",
    "protocols/corporate-event-alpha-v1/repurchase-five-clock-inventory-policy-v1.json",
}


def one_row(
    action: dict[str, Any], archive: dict[str, Any], membership: dict[str, Any], acceptance_raw: str
) -> dict[str, Any]:
    accession = action["accession"]
    if (
        accession != membership["accession"]
        or accession not in archive["target_accessions"]
        or not re.fullmatch(r"[0-9]{14}", acceptance_raw)
    ):
        raise ValueError("CLOCK_INVENTORY_IDENTITY_OR_ACCEPTANCE_INVALID")
    announced = action["facts"]["stated_announcement_date"]
    filed = action["source_filed"]
    archive_day = archive["archive_date_label"]
    if any(
        not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value)
        for value in (announced, filed, archive_day)
    ):
        raise ValueError("CLOCK_INVENTORY_DATE_INVALID")
    try:
        stated_date, filed_date, archive_date = map(
            date.fromisoformat, (announced, filed, archive_day)
        )
    except (TypeError, ValueError):
        raise ValueError("CLOCK_INVENTORY_DATE_INVALID") from None
    if not membership["member_sha256"] or membership["internal_accession_verified"] is not True:
        raise ValueError("CLOCK_INVENTORY_MEMBERSHIP_UNPROVEN")
    component = membership["component_comparison"]
    return {
        "case_key": action["case_key"],
        "company": action["company"],
        "accession": accession,
        "action_kind": action["action_kind"],
        "stated_announcement_date": announced,
        "source_filed_date": filed,
        "archive_date_label": archive_day,
        "stated_date_precedes_archive_label": stated_date < archive_date,
        "filing_date_matches_archive_label": filed_date == archive_date,
        "raw_acceptance_datetime": acceptance_raw,
        "acceptance_timezone_verified": False,
        "normalized_acceptance_datetime": None,
        "archive_member_sha256": membership["member_sha256"],
        "tar_header_checksum_verified": True,
        "full_gzip_checksum_verified": membership["full_gzip_checksum_verified"],
        "component_comparison": component,
        "source_claim_is_public_time_proof": False,
        "archive_label_is_dissemination_day_proof": False,
        "first_public_disclosure_verified": False,
        "registered_public_datetime": None,
        "classification_available_at": None,
        "public_time_status": "UNRESOLVED",
        "independent_factual_review_completed": False,
        "model_row_eligible": False,
    }


def run() -> dict[str, Any]:
    sec.preflight()
    registry = json.loads(
        (PROTOCOL / "repurchase-five-clock-inventory-extension-v1.json").read_bytes()
    )
    if set(registry["files"]) != REGISTERED:
        raise ValueError("CLOCK_INVENTORY_REGISTRY_SCOPE_INVALID")
    for name, digest in registry["files"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("CLOCK_INVENTORY_REGISTERED_FILE_CHANGED")
    policy = json.loads((PROTOCOL / "repurchase-five-clock-inventory-policy-v1.json").read_bytes())
    for name, digest in policy["private_inputs"].items():
        if ranges.sha_file(PRIVATE / name) != digest:
            raise ValueError("CLOCK_INVENTORY_PINNED_INPUT_CHANGED")
    for name, digest in policy["dependencies"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("CLOCK_INVENTORY_DEPENDENCY_CHANGED")
    sec.EXTENSION_FILES = (*sec.EXTENSION_FILES, *REGISTERED, *policy["dependencies"])
    sec.install_firewall()
    sys.addaudithook(
        lambda event, args: (
            (_ for _ in ()).throw(RuntimeError("CLOCK_INVENTORY_NETWORK_DENIED"))
            if event.startswith(("socket.", "http.client."))
            else None
        )
    )
    actions = [
        json.loads(line)
        for line in (PRIVATE / "repurchase-context-resolution-v1/actions.jsonl")
        .read_bytes()
        .splitlines()
    ]
    worklist = json.loads((PRIVATE / "repurchase-timing-evidence-worklist-v1.json").read_bytes())
    first = json.loads((PRIVATE / "two-archive-dissemination-evidence-ledger-v1.json").read_bytes())
    middle = json.loads((PRIVATE / "partial-feed-membership-evidence-v1.json").read_bytes())
    partial_state = json.loads((PRIVATE / "partial-feed-search-v1/state.json").read_bytes())
    partial_replay = json.loads(
        (PRIVATE / "partial-feed-interrupted-replay-v1/summary.json").read_bytes()
    )
    final = json.loads((PRIVATE / "continuation-forensic-replay-v1/summary.json").read_bytes())
    source_manifest = json.loads(
        (PRIVATE / "repurchase-acquisition-v1/acquired-source-manifest.json").read_bytes()
    )
    sources = {s["accession"]: s for s in source_manifest["sources"]}
    memberships: dict[str, dict[str, Any]] = {}
    for item in first["records"]:
        memberships[item["accession"]] = {
            "accession": item["accession"],
            "member_sha256": item["member_sha256"],
            "source_sha256": item["original_filing_sha256"],
            "internal_accession_verified": item["internal_accession_verified"],
            "full_gzip_checksum_verified": item["gzip_checksum_verified"],
            "component_comparison": item["component_comparison"],
        }
    partial_proofs = [m for s in partial_state["searches"] for m in s["proof"]["matches"]]
    partial_proofs.extend(partial_replay["result"]["matches"])
    for item in middle["records"]:
        matches = [m for m in partial_proofs if m["accession"] == item["accession"]]
        if (
            len(matches) != 1
            or matches[0]["member_body_sha256"] != item["member_sha256"]
            or matches[0]["source_sha256"] != item["source_sha256"]
            or matches[0]["tar_header_checksum_verified"] is not True
            or matches[0]["internal_accession_verified"] is not True
        ):
            raise ValueError("CLOCK_INVENTORY_PARTIAL_MEMBERSHIP_UNVERIFIED")
        memberships[item["accession"]] = {
            "accession": item["accession"],
            "member_sha256": item["member_sha256"],
            "source_sha256": item["source_sha256"],
            "internal_accession_verified": matches[0]["internal_accession_verified"],
            "full_gzip_checksum_verified": item["full_gzip_checksum_verified"],
            "component_comparison": item["document_comparison"],
        }
    for item in final["scan"]["matches"]:
        if not item["tar_header_checksum_verified"]:
            raise ValueError("CLOCK_INVENTORY_FINAL_HEADER_UNVERIFIED")
        memberships[item["accession"]] = {
            "accession": item["accession"],
            "member_sha256": item["member_body_sha256"],
            "source_sha256": item["source_sha256"],
            "internal_accession_verified": item["internal_accession_verified"],
            "full_gzip_checksum_verified": final["scan"]["gzip_eof_and_checksum_verified"],
            "component_comparison": item["component_comparison"],
        }
    if set(memberships) != set(ACCESSIONS):
        raise ValueError("CLOCK_INVENTORY_FIVE_MEMBERS_REQUIRED")
    rows = []
    for accession in ACCESSIONS:
        candidates = [
            a
            for a in actions
            if a["accession"] == accession and a["facts"].get("stated_announcement_date")
        ]
        archives = [a for a in worklist["targets"] if accession in a["target_accessions"]]
        if len(candidates) != 1 or len(archives) != 1:
            raise ValueError("CLOCK_INVENTORY_AMBIGUOUS_ACTION_OR_ARCHIVE")
        if sources[accession]["sha256"] != memberships[accession]["source_sha256"]:
            raise ValueError("CLOCK_INVENTORY_SOURCE_HASH_MISMATCH")
        raw = sec.Cache(PRIVATE / "repurchase-acquisition-v1").read(sources[accession]["sha256"])
        accepts = re.findall(
            rb"(?im)^(?:<ACCEPTANCE-DATETIME>|ACCEPTANCE-DATETIME:)[ \t]*([0-9]{14})", raw[:100_000]
        )
        if len(accepts) != 1:
            raise ValueError("CLOCK_INVENTORY_ACCEPTANCE_NOT_UNIQUE")
        rows.append(
            one_row(candidates[0], archives[0], memberships[accession], accepts[0].decode())
        )
    result = {
        "status": "FIVE_CANDIDATE_CLOCK_INPUTS_INVENTORIED",
        "records": rows,
        "candidate_filings": len(rows),
        "stated_dates_before_archive_labels": sum(
            x["stated_date_precedes_archive_label"] for x in rows
        ),
        "full_archive_checksums_verified": sum(x["full_gzip_checksum_verified"] for x in rows),
        "components_with_unresolved_differences": sum(
            x["component_comparison"]["nonmatching_components_require_review"] for x in rows
        ),
        "public_times_certified": 0,
        "first_public_announcements_verified": 0,
        "model_rows_eligible": 0,
        "new_HTTP_requests": 0,
        "regressions_run": 0,
        "original_718_sample_changes": 0,
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
        result = run()
        print(json.dumps({k: v for k, v in result.items() if k != "records"}), flush=True)
    except Exception as error:
        print(json.dumps({"status": "CLOCK_INVENTORY_STOPPED", "reason": base.safe_reason(error)}))
        raise SystemExit(2) from None
