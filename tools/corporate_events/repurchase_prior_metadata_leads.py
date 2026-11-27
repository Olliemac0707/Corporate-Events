"""Find bounded earlier filing metadata leads from already cached SEC search pages."""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sec_timing_diagnostics as base  # noqa: E402
import sec_two_archive_ranges as ranges  # noqa: E402

sec = base.sec
ROOT, PRIVATE, PROTOCOL = base.ROOT, base.PRIVATE, base.PROTOCOL
OUTPUT = PRIVATE / "repurchase-prior-metadata-leads-v1"
REGISTERED = {
    "tools/corporate_events/repurchase_prior_metadata_leads.py",
    "tests/test_repurchase_prior_metadata_leads.py",
    "protocols/corporate-event-alpha-v1/repurchase-prior-metadata-leads-policy-v1.json",
}


def select(
    clock: dict[str, Any], action: dict[str, Any], records: list[dict[str, Any]]
) -> dict[str, Any]:
    if clock["accession"] != action["accession"] or clock["case_key"] != action["case_key"]:
        raise ValueError("PRIOR_LEADS_ACTION_IDENTITY_MISMATCH")
    try:
        start = date.fromisoformat(clock["stated_announcement_date"]) - timedelta(days=7)
        end = date.fromisoformat(clock["source_filed_date"])
        cik = str(int(action["reporting_filer_cik"]))
    except (ValueError, TypeError):
        raise ValueError("PRIOR_LEADS_INVALID_DATES_OR_CIK") from None
    grouped: dict[str, dict[str, Any]] = {}
    for item in records:
        try:
            filed = date.fromisoformat(item["filed"])
            ciks = {str(int(c)) for c in item["search_ciks"]}
        except (KeyError, ValueError, TypeError):
            raise ValueError("PRIOR_LEADS_METADATA_INVALID") from None
        if not (start <= filed <= end and cik in ciks) or item["accession"] == clock["accession"]:
            continue
        if "8-K" not in item["root_forms"]:
            raise ValueError("PRIOR_LEADS_UNEXPECTED_ROOT_FORM")
        group = grouped.setdefault(
            item["accession"],
            {
                "accession": item["accession"],
                "filed": item["filed"],
                "search_hit_ids": [],
                "search_CIK_role": "REQUIRES_ISSUER_IDENTITY_VERIFICATION",
                "source_contents_reviewed": False,
                "public_time_certified": False,
            },
        )
        if group["filed"] != item["filed"]:
            raise ValueError("PRIOR_LEADS_CONFLICTING_FILED_DATE")
        group["search_hit_ids"].append(item["search_hit_id"])
    ordered = sorted(grouped.values(), key=lambda r: (r["filed"], r["accession"]))
    for group in ordered:
        group["search_hit_ids"] = sorted(set(group["search_hit_ids"]))
    return {
        "case_key": clock["case_key"],
        "accession": clock["accession"],
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "matched_index_filer_cik": cik,
        "metadata_candidate_accessions": len(ordered),
        "candidates": ordered,
        "absence_of_other_disclosure_proven": False,
        "first_public_time_certified": False,
    }


def run() -> dict[str, Any]:
    sec.preflight()
    registry = json.loads(
        (PROTOCOL / "repurchase-prior-metadata-leads-extension-v1.json").read_bytes()
    )
    if set(registry["files"]) != REGISTERED:
        raise ValueError("PRIOR_LEADS_REGISTRY_SCOPE_INVALID")
    for name, digest in registry["files"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("PRIOR_LEADS_REGISTERED_FILE_CHANGED")
    policy = json.loads((PROTOCOL / "repurchase-prior-metadata-leads-policy-v1.json").read_bytes())
    for name, digest in policy["private_inputs"].items():
        if ranges.sha_file(PRIVATE / name) != digest:
            raise ValueError("PRIOR_LEADS_PINNED_INPUT_CHANGED")
    for name, digest in policy["dependencies"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("PRIOR_LEADS_DEPENDENCY_CHANGED")
    sec.EXTENSION_FILES = (*sec.EXTENSION_FILES, *REGISTERED, *policy["dependencies"])
    sec.install_firewall()
    sys.addaudithook(
        lambda event, args: (
            (_ for _ in ()).throw(RuntimeError("PRIOR_LEADS_NETWORK_DENIED"))
            if event.startswith(("socket.", "http.client."))
            else None
        )
    )
    inventory = json.loads(
        (PRIVATE / "repurchase-five-clock-inventory-v1/summary.json").read_bytes()
    )
    actions = [
        json.loads(line)
        for line in (PRIVATE / "repurchase-context-resolution-v1/actions.jsonl")
        .read_bytes()
        .splitlines()
    ]
    records = []
    for name in policy["metadata_months"]:
        partition = json.loads((PRIVATE / "repurchase-discovery-v1/months" / name).read_bytes())
        if (
            partition["complete"] is not True
            or partition["unique_coverage_equals_exact_total"] is not True
            or f"{partition['year']:04d}-{partition['month']:02d}.json" != name
        ):
            raise ValueError("PRIOR_LEADS_INCOMPLETE_CACHED_PARTITION")
        records.extend(partition["records"])
    rows = []
    for clock in inventory["records"]:
        candidates = [
            a
            for a in actions
            if a["case_key"] == clock["case_key"] and a["accession"] == clock["accession"]
        ]
        if len(candidates) != 1:
            raise ValueError("PRIOR_LEADS_AMBIGUOUS_ACTION")
        rows.append(select(clock, candidates[0], records))
    result = {
        "status": "BOUNDED_CACHED_METADATA_LEADS_IDENTIFIED",
        "cases": rows,
        "candidate_cases": len(rows),
        "cases_with_metadata_leads": sum(bool(row["candidates"]) for row in rows),
        "metadata_candidate_accessions": sum(row["metadata_candidate_accessions"] for row in rows),
        "new_HTTP_requests": 0,
        "filing_bodies_downloaded": 0,
        "first_public_times_certified": 0,
        "original_718_sample_changes": 0,
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
        result = run()
        print(json.dumps({k: v for k, v in result.items() if k != "cases"}), flush=True)
    except Exception as error:
        print(json.dumps({"status": "PRIOR_LEADS_STOPPED", "reason": base.safe_reason(error)}))
        raise SystemExit(2) from None
