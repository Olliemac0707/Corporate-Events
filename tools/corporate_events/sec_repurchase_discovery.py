"""Complete monthly search metadata, then rank a bounded index-reconciled source manifest."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "reports/corporate-event-alpha-v1/phase2-private"
PROTOCOL = ROOT / "protocols/corporate-event-alpha-v1"
OUTPUT = PRIVATE / "repurchase-discovery-v1"
METADATA = PRIVATE / "snapshots/sec-index-metadata-v1-cached-completion-20261108T181347Z"
AUTHORITY_SHA = "2e00caafed8eb2ff3c5fd4410f1a8c592b6ac622640c1d4f2989783cb4237894"
REGISTERED = (
    "tools/corporate_events/sec_search_metadata_client_v3.py",
    "tools/corporate_events/sec_repurchase_discovery.py",
    "tests/test_sec_repurchase_discovery.py",
    "protocols/corporate-event-alpha-v1/sec-paged-discovery-policy-v1.json",
)


def load(name: str, path: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sec = load("paged_discovery_sec", "tools/corporate_events/sec_census.py")
client = load("paged_discovery_client", "tools/corporate_events/sec_search_metadata_client_v3.py")
# Accession parsing/metadata projection are preserved in the prior export implementation.
export = load("paged_discovery_export", "tools/corporate_events/sec_search_export.py")


def parse_page(raw: bytes, payload: dict[str, Any]) -> dict[str, Any]:
    client.validate_payload(payload)
    data = json.loads(raw)
    if (
        data.get("timed_out") is True
        or data.get("error")
        or data.get("_shards", {}).get("failed", 0)
    ):
        raise ValueError("SEARCH_TIMEOUT_ERROR_OR_SHARD_FAILURE")
    query = data["query"]
    if query.get("from", 0) != payload["from"] or query.get("size") != 100:
        raise ValueError("SEARCH_SERVER_PAGINATION_MISMATCH")
    excluded = query.get("_source", {}).get("exclude", [])
    if "doc_text" not in excluded:
        raise ValueError("SEARCH_TEXT_EXCLUSION_NOT_CONFIRMED")
    filters = query["query"]["bool"]["filter"]
    if not any(x.get("terms", {}).get("root_forms") == ["8-K"] for x in filters):
        raise ValueError("SEARCH_ROOT_FORM_FILTER_NOT_CONFIRMED")
    if any("form" in x.get("terms", {}) for x in filters):
        raise ValueError("SEARCH_UNINTENDED_DOCUMENT_FORM_FILTER")
    ranges = [x["range"]["file_date"] for x in filters if "file_date" in x.get("range", {})]
    if ranges != [{"gte": payload["startdt"], "lte": payload["enddt"]}]:
        raise ValueError("SEARCH_SERVER_DATE_FILTER_MISMATCH")
    total = data["hits"]["total"]
    if not isinstance(total, dict) or total.get("relation") != "eq":
        raise ValueError("SEARCH_TOTAL_NOT_EXACT")
    count = total["value"]
    if type(count) is not int or not 0 <= count < 10000:
        raise ValueError("SEARCH_MONTH_EXCEEDS_RESULT_WINDOW")
    hits = data["hits"]["hits"]
    expected = min(100, max(0, count - payload["from"]))
    if not isinstance(hits, list) or len(hits) != expected:
        raise ValueError("SEARCH_TRUNCATED_OR_WRONG_PAGE")
    # Reuse strict field projection; do not carry highlights or body text into the frame.
    for hit in hits:
        if any(key in hit["_source"] for key in ("doc_text", "body", "content")):
            raise ValueError("SEARCH_UNEXPECTED_BODY_FIELD")
    projected = export.metadata(
        sec.json_bytes({"hits": {"total": {"value": len(hits), "relation": "eq"}, "hits": hits}}),
        int(payload["startdt"][:4]),
    )
    for row in projected["records"]:
        if not payload["startdt"] <= row["filed"] <= payload["enddt"]:
            raise ValueError("SEARCH_HIT_OUTSIDE_MONTH")
    return {
        "total": count,
        "records": projected["records"],
        "raw_sha256": sec.sha(raw),
        "offset": payload["from"],
    }


def verify_registration() -> dict[str, Any]:
    protocol = sec.preflight()
    sample = PRIVATE / "parser-qa-v1/runs/development-parse-v2/development-qa-selection.json"
    if sec.file_sha(sample) != "5da0f6c6061ebea6ac08e57e598e670636d634dd7b1dc158767fba135be073ae":
        raise ValueError("ORIGINAL_QA_SAMPLE_CHANGED")
    registry = json.loads((PROTOCOL / "sec-paged-discovery-extension-v1.json").read_bytes())
    if set(registry["files"]) != set(REGISTERED):
        raise ValueError("PAGED_REGISTRY_SCOPE_INVALID")
    for name, expected in registry["files"].items():
        if sec.file_sha(ROOT / name) != expected:
            raise ValueError("PAGED_REGISTERED_CODE_CHANGED")
    policy = json.loads((PROTOCOL / "sec-paged-discovery-policy-v1.json").read_bytes())
    if (
        sec.file_sha(PROTOCOL / "repurchase-first-pilot-plan-v1.json")
        != policy["parent_plan_sha256"]
    ):
        raise ValueError("PARENT_SELECTION_PLAN_CHANGED")
    old = json.loads((PROTOCOL / "sec-search-export-extension-v1.json").read_bytes())
    for name, expected in old["files"].items():
        if sec.file_sha(ROOT / name) != expected:
            raise ValueError("PREDECESSOR_EXPORT_CODE_CHANGED")
    route = export.verify_route()
    return {"protocol": protocol, "registry": registry, "route": route}


def guard() -> None:
    sec.ALLOWED_HOSTS = sec.ALLOWED_HOSTS | {"efts.sec.gov"}
    sec.EXTENSION_FILES = (
        *sec.EXTENSION_FILES,
        *REGISTERED,
        *json.loads((PROTOCOL / "sec-search-export-extension-v1.json").read_bytes())["files"],
    )
    sec.install_firewall()


def immutable(path: Path, value: dict[str, Any]) -> None:
    raw = sec.json_bytes(value)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("IMMUTABLE_DISCOVERY_OUTPUT_CONFLICT")
    else:
        sec.write_new(path, raw)


def crawl(probe: bool = False) -> dict[str, Any]:
    proof = verify_registration()
    agent = sec.private_contact(ROOT)
    guard()
    (OUTPUT / "months").mkdir(exist_ok=True)
    sec.append_record(
        OUTPUT / "paged-preflights.jsonl",
        {
            **proof,
            "private_contact": "VALID_VALUE_NOT_LOGGED",
            "mode": "DIAGNOSTIC" if probe else "BOUNDED_METADATA_CRAWL",
            "old_budgets_reset": False,
            "filing_bodies_allowed": False,
        },
    )
    partitions = [(2006, 1)] if probe else [(y, m) for y in range(2006, 2016) for m in range(1, 13)]
    completed = []
    phase, cap, limit = (
        ("root-profile-diagnostic", 1, 8388608) if probe else ("crawl", 1500, 134217728)
    )
    with sec.Scheduler(PRIVATE) as scheduler:
        transport = client.Client(sec, OUTPUT, scheduler, agent, phase, cap, limit)
        for year, month in partitions:
            path = OUTPUT / "months" / f"{year}-{month:02d}.json"
            if path.exists():
                saved = json.loads(path.read_bytes())
                if saved.get("complete") is not True:
                    raise ValueError("INCOMPLETE_MONTH_CANNOT_BE_REUSED")
                completed.append(saved)
                continue
            first = parse_page(
                transport.fetch(client.SEARCH, "GET", client.search_payload(year, month)),
                client.search_payload(year, month),
            )
            if probe:
                result = {
                    "status": "PAGED_ROOT_FORM_PROFILE_LIVE_VALIDATED",
                    "year": year,
                    "month": month,
                    "total_document_hits": first["total"],
                    "first_page_hits": len(first["records"]),
                    "raw_sha256": first["raw_sha256"],
                    "form_filter": "root_forms=8-K; amendments included; no form-only restriction",
                    "filing_bodies_downloaded": 0,
                }
                immutable(OUTPUT / "root-profile-probe.json", result)
                print(json.dumps(result), flush=True)
                return result
            records = list(first["records"])
            pages = [first["raw_sha256"]]
            seen = {row["search_hit_id"] for row in records}
            for offset in range(100, first["total"], 100):
                payload = client.search_payload(year, month, offset)
                page = parse_page(transport.fetch(client.SEARCH, "GET", payload), payload)
                if page["total"] != first["total"]:
                    raise ValueError("SEARCH_TOTAL_CHANGED_WITHIN_PARTITION")
                for row in page["records"]:
                    if row["search_hit_id"] in seen:
                        raise ValueError("SEARCH_DUPLICATE_ACROSS_PAGES")
                    seen.add(row["search_hit_id"])
                records.extend(page["records"])
                pages.append(page["raw_sha256"])
            if len(records) != first["total"]:
                raise ValueError("INCOMPLETE_MONTH")
            saved = {
                "year": year,
                "month": month,
                "complete": True,
                "document_hits": first["total"],
                "records": records,
                "raw_page_sha256": pages,
                "query": client.search_payload(year, month),
            }
            immutable(path, saved)
            completed.append(saved)
            print(
                json.dumps(
                    {
                        "partition": f"{year}-{month:02d}",
                        "document_hits": len(records),
                        "completed_months": len(completed),
                        "crawl_HTTP_requests": transport.state["requests"],
                    }
                ),
                flush=True,
            )
    result = {
        "status": "ALL_120_MONTHS_COMPLETE",
        "months": len(completed),
        "document_hits": sum(x["document_hits"] for x in completed),
        "crawl_HTTP_requests": transport.state["requests"],
        "crawl_received_bytes": transport.state["bytes"],
        "filing_bodies_downloaded": 0,
    }
    immutable(OUTPUT / "crawl-completion.json", result)
    print(json.dumps(result), flush=True)
    return result


def rank(plan_sha: str, year: int, role: str, filename: str) -> str:
    return hashlib.sha256(f"{plan_sha}|{year}|{role}|{filename}".encode()).hexdigest()


def bounded(rows: list[dict[str, Any]], quota: int) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for row in sorted(rows, key=lambda x: (x["selection_rank"], x["index_filename"])):
        if row["accession"] in seen:
            continue
        result.append(row)
        seen.add(row["accession"])
        if len(result) == quota:
            break
    return result


def freeze() -> dict[str, Any]:
    proof = verify_registration()
    sys.addaudithook(
        lambda event, args: (
            (_ for _ in ()).throw(RuntimeError("MANIFEST_OFFLINE_NETWORK_DENIED"))
            if event.startswith(("socket.", "http.client."))
            else None
        )
    )
    plan_path = PROTOCOL / "repurchase-first-pilot-plan-v1.json"
    plan_sha = sec.file_sha(plan_path)
    hit_by_acc: dict[str, list[dict[str, Any]]] = {}
    counts: dict[int, dict[str, int]] = {}
    for year in range(2006, 2016):
        counts[year] = {"search_document_hits": 0}
        for month in range(1, 13):
            data = json.loads((OUTPUT / "months" / f"{year}-{month:02d}.json").read_bytes())
            if not data.get("complete") or len(data["records"]) != data["document_hits"]:
                raise ValueError("MANIFEST_REQUIRES_ALL_COMPLETE_MONTHS")
            counts[year]["search_document_hits"] += data["document_hits"]
            for row in data["records"]:
                hit_by_acc.setdefault(row["accession"], []).append(row)
    authority_path = METADATA / "authority-manifest.json"
    if sec.file_sha(authority_path) != AUTHORITY_SHA:
        raise ValueError("HISTORICAL_INDEX_AUTHORITY_CHANGED")
    authority = json.loads(authority_path.read_bytes())
    source_path = METADATA / "source-objects.jsonl"
    if sec.file_sha(source_path) != authority["files"]["source-objects.jsonl"]:
        raise ValueError("HISTORICAL_SOURCE_REGISTRY_CHANGED")
    controls: dict[int, list[dict[str, Any]]] = {y: [] for y in range(2006, 2016)}
    candidates: dict[str, list[dict[str, Any]]] = {}
    processed = []
    unique_paths: set[str] = set()
    frame_counts: Counter[int] = Counter()
    cache = sec.Cache(PRIVATE)
    for obj in [json.loads(line) for line in source_path.read_bytes().splitlines()]:
        if not 2006 <= obj["year"] <= 2015:
            continue
        raw = cache.read(obj["sha256"])
        content, encoding = sec.index_payload(raw, obj["url"])
        processed.append({"url": obj["url"], "raw_sha256": obj["sha256"]})
        for source in sec.parse_master(content, encoding):
            if (
                source.form not in {"8-K", "8-K/A"}
                or not "2006-01-01" <= source.filed <= "2015-12-31"
            ):
                continue
            if source.filename in unique_paths:
                continue
            unique_paths.add(source.filename)
            acc = export.accession(source.filename.rsplit("/", 1)[-1].removesuffix(".txt"))
            year = int(source.filed[:4])
            frame_counts[year] += 1
            row = {
                "accession": acc,
                "index_filename": source.filename,
                "cik_from_historical_index": source.cik,
                "cik_role": "INDEX_FILER_NOT_AUTOMATICALLY_ISSUER",
                "company_name_as_indexed": source.company_name,
                "form": source.form,
                "filed": source.filed,
                "year": year,
                "index_url": obj["url"],
                "index_raw_sha256": obj["sha256"],
                "filing_date_is_public_time": False,
            }
            control = {
                **row,
                "role": "SOURCE_CONTROL",
                "selection_rank": rank(plan_sha, year, "SOURCE_CONTROL", source.filename),
            }
            controls[year] = bounded([*controls[year], control], 2)
            if acc in hit_by_acc:
                matches = [
                    x
                    for x in hit_by_acc[acc]
                    if x["filed"] == source.filed and str(int(source.cik)) in x["search_ciks"]
                ]
                if matches:
                    candidates.setdefault(acc, []).append(
                        {
                            **row,
                            "role": "CANDIDATE_HIT",
                            "selection_rank": rank(
                                plan_sha, year, "CANDIDATE_HIT", source.filename
                            ),
                            "search_hit_ids": sorted({x["search_hit_id"] for x in matches}),
                            "search_CIKs_not_issuer_certification": True,
                        }
                    )
        print(
            json.dumps({"index_verified": obj["url"], "eligible_paths": len(unique_paths)}),
            flush=True,
        )
    if len(processed) != 40:
        raise ValueError("FORTY_HISTORICAL_QUARTERS_REQUIRED")
    control_rows = [x for year in sorted(controls) for x in controls[year]]
    control_acc = {x["accession"] for x in control_rows}
    selected = []
    for year in range(2006, 2016):
        pool = [
            row
            for acc, values in candidates.items()
            if acc not in control_acc
            for row in values
            if row["year"] == year
        ]
        chosen = bounded(pool, 8)
        selected.extend(chosen)
        counts[year].update(
            historical_8K_paths=frame_counts[year],
            index_reconciled_candidate_accessions=len({x["accession"] for x in pool}),
            selected_candidates=len(chosen),
            selected_controls=len(controls[year]),
        )
    entries = selected + control_rows
    if len({x["accession"] for x in entries}) != len(entries) or len(entries) > 100:
        raise ValueError("MANIFEST_ACCESSION_OVERLAP_OR_CAP")
    for row in entries:
        row.update(
            source_url="https://www.sec.gov/Archives/" + row["index_filename"],
            keyword_search_hit=row["accession"] in hit_by_acc,
            event_presence="NOT_ASSESSED",
            body_status="NOT_DOWNLOADED",
            body_sha256=None,
            research_event_accepted=False,
        )
    ready = len(selected) == 80 and len(control_rows) == 20
    manifest = {
        "version": "bounded-repurchase-source-manifest-v1",
        "status": "METADATA_VALIDATED_READY_FOR_SEPARATE_BOUNDED_ACQUISITION"
        if ready
        else "INCOMPLETE_WITH_EXPLICIT_SHORTFALLS",
        "plan_sha256": plan_sha,
        "metadata_authority_sha256": AUTHORITY_SHA,
        "source_period": ["2006-01-01", "2015-12-31"],
        "entries": sorted(entries, key=lambda x: (x["year"], x["role"], x["selection_rank"])),
        "counts_by_year": counts,
        "selected_candidates": len(selected),
        "selected_controls": len(control_rows),
        "selected_forms": dict(Counter(x["form"] for x in entries)),
        "total_selected": len(entries),
        "source_limit": 100,
        "future_body_byte_cap": 536870912,
        "historical_source_objects": processed,
        "search_accessions_not_index_reconciled": sorted(set(hit_by_acc) - set(candidates)),
        "control_selection_independent_of_keyword_hit": True,
        "controls_are_not_assumed_negative": True,
        "query_hits_are_not_certified_events": True,
        "all_response_pages_metadata_only": True,
        "filing_bodies_downloaded": 0,
        "filing_body_downloads_authorized_by_this_command": False,
        "market_data_allowed": False,
        "original_718_sample_changes": 0,
        "full_extraction_authorized": False,
        "final_qa_authorized": False,
        "phase3_authorized": False,
        "protocol_preflight": proof["protocol"],
    }
    immutable(OUTPUT / "bounded-source-manifest.json", manifest)
    immutable(
        OUTPUT / "bounded-source-manifest-binding.json",
        {
            "manifest_sha256": sec.file_sha(OUTPUT / "bounded-source-manifest.json"),
            "plan_sha256": plan_sha,
            "code_registry": json.loads(
                (PROTOCOL / "sec-paged-discovery-extension-v1.json").read_bytes()
            ),
        },
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in manifest.items()
                if k
                not in {
                    "entries",
                    "historical_source_objects",
                    "protocol_preflight",
                    "search_accessions_not_index_reconciled",
                }
            }
        ),
        flush=True,
    )
    return manifest


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("command", choices=["probe", "crawl", "freeze"])
    command = cli.parse_args().command
    try:
        if command == "freeze":
            freeze()
        else:
            crawl(command == "probe")
    except (ValueError, OSError, KeyError, RuntimeError, sec.AcquisitionError) as error:
        safe = (
            str(error)
            if isinstance(error, (ValueError, sec.AcquisitionError))
            else type(error).__name__
        )
        print(
            json.dumps({"status": "DISCOVERY_STOPPED_NO_READY_MANIFEST", "reason": safe}),
            flush=True,
        )
        raise SystemExit(2) from None
