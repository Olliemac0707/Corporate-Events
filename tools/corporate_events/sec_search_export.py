"""Read complete annual SEC search metadata exports under the fixed twelve-request cap."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "reports/corporate-event-alpha-v1/phase2-private"
PROTOCOL = ROOT / "protocols/corporate-event-alpha-v1"
OUTPUT = PRIVATE / "repurchase-discovery-v1"
REGISTERED = (
    "tools/corporate_events/sec_search_metadata_client_v2.py",
    "tools/corporate_events/sec_search_export.py",
    "tests/test_sec_search_export.py",
    "protocols/corporate-event-alpha-v1/sec-search-export-policy-v1.json",
)


def load(name: str, path: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sec = load("search_export_sec", "tools/corporate_events/sec_census.py")
client = load("search_export_transport", "tools/corporate_events/sec_search_metadata_client_v2.py")


def accession(value: str) -> str:
    if re.fullmatch(r"[0-9]{18}", value):
        value = value[:10] + "-" + value[10:12] + "-" + value[12:]
    if not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", value):
        raise ValueError("SEARCH_ACCESSION_INVALID")
    return value


def metadata(raw: bytes, year: int) -> dict[str, Any]:
    data = json.loads(raw)
    if data.get("error") or data.get("timed_out") is True:
        raise ValueError("SEARCH_ERROR_OR_TIMEOUT")
    shard = data.get("_shards", {})
    if shard.get("failed", 0):
        raise ValueError("SEARCH_FAILED_SHARDS")
    section = data["hits"]
    total = section["total"]
    if isinstance(total, dict):
        if total.get("relation") != "eq":
            raise ValueError("SEARCH_TOTAL_NOT_EXACT")
        total = total["value"]
    if type(total) is not int or total < 0:
        raise ValueError("SEARCH_TOTAL_INVALID")
    hits = section["hits"]
    if not isinstance(hits, list) or len(hits) != total or total > 10000:
        raise ValueError("SEARCH_EXPORT_TRUNCATED")
    seen = set()
    records = []
    for hit in hits:
        identity = hit["_id"]
        if not isinstance(identity, str) or identity in seen:
            raise ValueError("SEARCH_DUPLICATE_OR_INVALID_HIT_ID")
        seen.add(identity)
        acc = accession(identity.split(":", 1)[0])
        source = hit["_source"]
        filed = source["file_date"]
        if not isinstance(filed, str) or date.fromisoformat(filed).year != year:
            raise ValueError("SEARCH_DATE_OUTSIDE_PARTITION")
        form = source.get("form")
        roots = source.get("root_forms", [])
        if form not in {"8-K", "8-K/A"} and "8-K" not in roots:
            raise ValueError("SEARCH_FORM_OUTSIDE_SCOPE")
        ciks = source.get("ciks", [])
        if not isinstance(ciks, list) or not ciks:
            raise ValueError("SEARCH_HISTORICAL_CIKS_MISSING")
        if any(not re.fullmatch(r"[0-9]{1,10}", str(cik)) for cik in ciks):
            raise ValueError("SEARCH_CIK_INVALID")
        records.append(
            {
                "search_hit_id": identity,
                "accession": acc,
                "filed": filed,
                "search_form": form,
                "root_forms": roots,
                "search_ciks": sorted({str(int(cik)) for cik in ciks}),
                "source_role_status": "SEARCH_CIKS_NOT_AUTOMATICALLY_ISSUER",
            }
        )
    return {
        "year": year,
        "reported_document_hits": total,
        "returned_document_hits": len(hits),
        "complete_export": True,
        "distinct_accessions": len({x["accession"] for x in records}),
        "records": records,
        "filing_date_is_public_time": False,
    }


def verify_route() -> dict[str, Any]:
    findings = [
        json.loads(line) for line in (OUTPUT / "bootstrap-results.jsonl").read_bytes().splitlines()
    ]
    proof = next(
        x for x in reversed(findings) if x["status"] == "OFFICIAL_SEARCH_ROUTING_SCRIPT_CACHED"
    )
    raw = sec.Cache(OUTPUT).read(proof["routing_script_sha256"])
    if (
        client.SEARCH.encode() not in raw
        or b"exportParams.narrow = 'export'" not in raw
        or b"MAX_MAI_EXPORT_SIZE = 10000" not in raw
        or b"type: 'GET'" not in raw
    ):
        raise ValueError("OFFICIAL_SCRIPT_ROUTE_OR_EXPORT_NOT_VERIFIED")
    return dict(proof)


def run(first_only: bool = False) -> dict[str, Any]:
    protocol = sec.preflight()
    ext = json.loads((PROTOCOL / "sec-search-export-extension-v1.json").read_bytes())
    if set(ext["files"]) != set(REGISTERED):
        raise ValueError("SEARCH_EXPORT_REGISTRY_SCOPE_INVALID")
    for name, expected in ext["files"].items():
        if sec.file_sha(ROOT / name) != expected:
            raise ValueError("SEARCH_EXPORT_CODE_HASH_MISMATCH")
    route = verify_route()
    agent = sec.private_contact(ROOT)
    # This additional host is the exact backend declared in the cached official site script.
    sec.ALLOWED_HOSTS = sec.ALLOWED_HOSTS | {"efts.sec.gov"}
    sec.EXTENSION_FILES = (*sec.EXTENSION_FILES, *REGISTERED)
    sec.install_firewall()
    (OUTPUT / "annual").mkdir(exist_ok=True)
    sec.append_record(
        OUTPUT / "export-preflights.jsonl",
        {
            "protocol": protocol,
            "registered_files": ext["files"],
            "route": route,
            "contact_status": "VALID_PRIVATE_VALUE_NOT_LOGGED",
            "diagnostic_and_export_total_request_cap": 12,
            "new_export_byte_cap": 134217728,
            "old_diagnostic_budget_not_reset": True,
        },
    )
    years = [2006] if first_only else list(range(2006, 2016))
    completed = []
    with sec.Scheduler(PRIVATE) as scheduler:
        transport = client.Client(sec, OUTPUT, scheduler, agent, "exports", 10, 134217728)
        for year in years:
            payload = client.search_payload(year)
            raw = transport.fetch(client.SEARCH, "GET", payload)
            parsed = metadata(raw, year)
            path = OUTPUT / "annual" / (str(year) + ".json")
            encoded = sec.json_bytes(
                {**parsed, "raw_response_sha256": sec.sha(raw), "request_payload": payload}
            )
            if path.exists():
                if path.read_bytes() != encoded:
                    raise ValueError("SEARCH_ANNUAL_METADATA_CHANGED")
            else:
                sec.write_new(path, encoded)
            completed.append({key: value for key, value in parsed.items() if key != "records"})
    result = {
        "status": "ANNUAL_METADATA_EXPORTS_VALIDATED",
        "years": completed,
        "export_requests_total": transport.state["requests"],
        "export_received_bytes": transport.state["bytes"],
        "filing_body_downloads": 0,
        "market_data_queries": 0,
        "scope": "Exact annual document-hit exports, not an exhaustive repurchase-event census",
    }
    sec.append_record(OUTPUT / "export-results.jsonl", result)
    print(json.dumps(result), flush=True)
    return result


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("--first-year-only", action="store_true")
    run(args.parse_args().first_year_only)
