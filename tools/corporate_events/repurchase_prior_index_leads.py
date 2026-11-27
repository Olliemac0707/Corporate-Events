"""Find bounded prior-filing leads in the already cached SEC quarterly master indexes."""

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
OUTPUT = PRIVATE / "repurchase-prior-index-leads-v1"
REGISTERED = {
    "tools/corporate_events/repurchase_prior_index_leads.py",
    "tests/test_repurchase_prior_index_leads.py",
    "protocols/corporate-event-alpha-v1/repurchase-prior-index-leads-policy-v1.json",
}


def select(
    clock: dict[str, Any], action: dict[str, Any], records: list[dict[str, Any]]
) -> dict[str, Any]:
    if clock["accession"] != action["accession"] or clock["case_key"] != action["case_key"]:
        raise ValueError("INDEX_LEADS_ACTION_IDENTITY_MISMATCH")
    try:
        start = date.fromisoformat(clock["stated_announcement_date"]) - timedelta(days=7)
        end = date.fromisoformat(clock["source_filed_date"])
        cik = str(int(action["reporting_filer_cik"]))
    except (ValueError, TypeError):
        raise ValueError("INDEX_LEADS_INVALID_DATES_OR_CIK") from None
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in records:
        try:
            filed = date.fromisoformat(item["filed"])
            row_cik = str(int(item["cik"]))
        except (KeyError, ValueError, TypeError):
            raise ValueError("INDEX_LEADS_METADATA_INVALID") from None
        if not (start <= filed <= end and row_cik == cik):
            continue
        if item["filename"].endswith("/" + clock["accession"] + ".txt"):
            continue
        key = (item["filename"], item["form"])
        grouped[key] = {
            "filing_path": item["filename"],
            "filed": item["filed"],
            "form": item["form"],
            "company_name_as_indexed": item["company"],
            "index_filer_role": "REQUIRES_ISSUER_IDENTITY_VERIFICATION",
            "source_contents_reviewed": False,
            "public_time_certified": False,
        }
    ordered = sorted(grouped.values(), key=lambda r: (r["filed"], r["filing_path"], r["form"]))
    return {
        "case_key": clock["case_key"],
        "accession": clock["accession"],
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "matched_index_filer_cik": cik,
        "metadata_candidate_rows": len(ordered),
        "candidates": ordered,
        "absence_of_other_disclosure_proven": False,
        "first_public_time_certified": False,
    }


def run() -> dict[str, Any]:
    sec.preflight()
    registry = json.loads(
        (PROTOCOL / "repurchase-prior-index-leads-extension-v1.json").read_bytes()
    )
    if set(registry["files"]) != REGISTERED:
        raise ValueError("INDEX_LEADS_REGISTRY_SCOPE_INVALID")
    for name, digest in registry["files"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("INDEX_LEADS_REGISTERED_FILE_CHANGED")
    policy = json.loads((PROTOCOL / "repurchase-prior-index-leads-policy-v1.json").read_bytes())
    for name, digest in policy["private_inputs"].items():
        if ranges.sha_file(PRIVATE / name) != digest:
            raise ValueError("INDEX_LEADS_PINNED_INPUT_CHANGED")
    for name, digest in policy["dependencies"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("INDEX_LEADS_DEPENDENCY_CHANGED")
    sec.EXTENSION_FILES = (*sec.EXTENSION_FILES, *REGISTERED, *policy["dependencies"])
    sec.install_firewall()
    sys.addaudithook(
        lambda event, args: (
            (_ for _ in ()).throw(RuntimeError("INDEX_LEADS_NETWORK_DENIED"))
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
    targets = []
    for clock in inventory["records"]:
        candidates = [
            a
            for a in actions
            if a["case_key"] == clock["case_key"] and a["accession"] == clock["accession"]
        ]
        if len(candidates) != 1:
            raise ValueError("INDEX_LEADS_AMBIGUOUS_ACTION")
        targets.append((clock, candidates[0]))
    allowed_ciks = {str(int(action["reporting_filer_cik"])) for _, action in targets}
    sources = json.loads((PRIVATE / "prior-index-leads-sources-v1.json").read_bytes())
    records, source_counts = [], []
    for source in sources["indexes"]:
        raw = sec.Cache(PRIVATE).read(source["raw_sha256"])
        payload, encoding = sec.index_payload(raw, source["url"])
        if sec.sha(payload) != source["index_sha256"] or encoding != source["encoding"]:
            raise ValueError("INDEX_LEADS_INDEX_HASH_OR_ENCODING_MISMATCH")
        count = 0
        for item in sec.parse_master(payload, encoding):
            count += 1
            if str(int(item.cik)) in allowed_ciks:
                records.append(
                    {
                        "cik": item.cik,
                        "company": item.company_name,
                        "form": item.form,
                        "filed": item.filed,
                        "filename": item.filename,
                    }
                )
        if count != source["raw_rows"]:
            raise ValueError("INDEX_LEADS_SOURCE_ROW_COUNT_MISMATCH")
        source_counts.append({"year": source["year"], "quarter": source["quarter"], "rows": count})
    rows = [select(clock, action, records) for clock, action in targets]
    result = {
        "status": "BOUNDED_CACHED_INDEX_LEADS_IDENTIFIED",
        "cases": rows,
        "index_sources": source_counts,
        "candidate_cases": len(rows),
        "cases_with_metadata_leads": sum(bool(row["candidates"]) for row in rows),
        "metadata_candidate_rows": sum(row["metadata_candidate_rows"] for row in rows),
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
        print(json.dumps({"status": "INDEX_LEADS_STOPPED", "reason": base.safe_reason(error)}))
        raise SystemExit(2) from None
