"""Offline replay of raw SEC metadata, coverage and bounded manifest provenance."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools/corporate_events" / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


m = load("discovery_audit_sorted", "sec_repurchase_discovery_v4.py")
legacy = load("discovery_audit_legacy", "sec_repurchase_discovery_v2.py")
sec = m.sec
AUDIT_FILES = {
    "tools/corporate_events/sec_discovery_audit_v2.py",
    "tests/test_sec_discovery_audit_v2.py",
    "protocols/corporate-event-alpha-v1/sec-discovery-audit-policy-v2.json",
}


def verify_audit_registration() -> dict[str, Any]:
    path = m.PROTOCOL / "sec-discovery-audit-extension-v2.json"
    registry: dict[str, Any] = json.loads(path.read_bytes())
    if set(registry["files"]) != AUDIT_FILES:
        raise ValueError("AUDIT_REGISTRY_SCOPE_INVALID")
    for name, expected in registry["files"].items():
        if sec.file_sha(ROOT / name) != expected:
            raise ValueError("AUDIT_CODE_OR_POLICY_CHANGED")
    for pattern in ("sec-search-*-extension-v*.json", "sec-paged-discovery-extension-v*.json"):
        for predecessor in m.PROTOCOL.glob(pattern):
            for name, expected in json.loads(predecessor.read_bytes())["files"].items():
                if sec.file_sha(ROOT / name) != expected:
                    raise ValueError("DISCOVERY_PREDECESSOR_REGISTRY_CHANGED")
    return registry


def verify_query(data: dict[str, Any]) -> None:
    boolean = data["query"]["query"]["bool"]
    if (
        boolean.get("must") != []
        or boolean.get("must_not") != []
        or boolean.get("minimum_should_match") != 1
        or boolean.get("should")
        != [{"match_phrase": {"doc_text": word}} for word in ("repurchase", "buyback", "buy back")]
    ):
        raise ValueError("SEARCH_QUERY_SEMANTICS_NOT_CONFIRMED")


def replay_month(saved: dict[str, Any], cache: Any) -> dict[str, Any]:
    year, month = saved["year"], saved["month"]
    if not 2006 <= year <= 2015 or not 1 <= month <= 12 or saved.get("complete") is not True:
        raise ValueError("MONTH_SCOPE_OR_COMPLETION_INVALID")
    proofs = saved.get("raw_page_proofs")
    sorted_profile = proofs is not None
    if not sorted_profile:
        if (year, month) not in {(2006, 1), (2006, 2)}:
            raise ValueError("UNREGISTERED_LEGACY_MONTH")
        proofs = [
            {"offset": i * 100, "raw_sha256": sha} for i, sha in enumerate(saved["raw_page_sha256"])
        ]
    if not proofs:
        raise ValueError("MISSING_RAW_PAGE_PROOFS")
    found: dict[str, Any] = {}
    for proof in proofs:
        raw = cache.read(proof["raw_sha256"])
        verify_query(json.loads(raw))
        parser = m if sorted_profile else legacy
        payload = (
            m.client.search_payload(year, month, proof["offset"], proof["sort"])
            if sorted_profile
            else legacy.client.search_payload(year, month, proof["offset"])
        )
        page = parser.parse_page(raw, payload)
        if page["total"] != saved["document_hits"]:
            raise ValueError("RAW_TOTAL_DIFFERS_FROM_MONTH")
        for row in page["records"]:
            key = row["search_hit_id"]
            if key in found and found[key] != row:
                raise ValueError("RAW_METADATA_CONFLICT")
            found[key] = row
    recorded = {row["search_hit_id"]: row for row in saved["records"]}
    if (
        len(recorded) != len(saved["records"])
        or len(found) != saved["document_hits"]
        or recorded != found
    ):
        raise ValueError("RAW_REPLAY_DOES_NOT_REPRODUCE_MONTH")
    return {"document_hits": len(found), "raw_pages": len(proofs)}


def verify_selection(manifest: dict[str, Any]) -> None:
    entries = manifest["entries"]
    counts = Counter((row["year"], row["role"]) for row in entries)
    if (
        len(entries) != 100
        or len({row["accession"] for row in entries}) != 100
        or counts
        != Counter(
            {
                (year, role): quota
                for year in range(2006, 2016)
                for role, quota in (("CANDIDATE_HIT", 8), ("SOURCE_CONTROL", 2))
            }
        )
    ):
        raise ValueError("SOURCE_QUOTAS_OR_UNIQUENESS_INVALID")
    for row in entries:
        if (
            row["form"] not in {"8-K", "8-K/A"}
            or row["body_status"] != "NOT_DOWNLOADED"
            or row["body_sha256"] is not None
            or row["research_event_accepted"] is not False
            or row["source_url"] != "https://www.sec.gov/Archives/" + row["index_filename"]
            or row["selection_rank"]
            != m.rank(manifest["plan_sha256"], row["year"], row["role"], row["index_filename"])
            or row["filing_date_is_public_time"] is not False
            or row["cik_role"] != "INDEX_FILER_NOT_AUTOMATICALLY_ISSUER"
        ):
            raise ValueError("SOURCE_ENTRY_PROVENANCE_OR_SCOPE_INVALID")


def stable_manifest(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(json.dumps(value))
    # These describe the original run, not the immutable selection algorithm.
    for name in ("code_commit", "tracked_changes_present"):
        result["protocol_preflight"].pop(name, None)
    return result


def replay_selection(manifest: dict[str, Any]) -> None:
    original = m.immutable

    def compare(path: Path, value: dict[str, Any]) -> None:
        if path.name == "bounded-source-manifest.json":
            if stable_manifest(value) != stable_manifest(manifest):
                raise ValueError("OFFLINE_SELECTION_REPLAY_MISMATCH")
        elif path.name == "bounded-source-manifest-binding.json":
            if json.loads(path.read_bytes()) != value:
                raise ValueError("OFFLINE_SELECTION_BINDING_REPLAY_MISMATCH")
        else:
            raise ValueError("UNEXPECTED_SELECTION_REPLAY_OUTPUT")

    m.immutable = compare
    try:
        m.freeze()
    finally:
        m.immutable = original


def audit() -> dict[str, Any]:
    registry = verify_audit_registration()
    current_proof = m.verify_registration()
    sec.append_record(m.OUTPUT / "audit-replay-preflights.jsonl", current_proof)
    sys.addaudithook(
        lambda event, args: (
            (_ for _ in ()).throw(RuntimeError("DISCOVERY_AUDIT_OFFLINE_NETWORK_DENIED"))
            if event.startswith(("socket.", "http.client."))
            else None
        )
    )
    cache = sec.Cache(m.OUTPUT)
    months = {}
    counts: Counter[int] = Counter()
    pages = 0
    for year in range(2006, 2016):
        for month in range(1, 13):
            path = m.OUTPUT / "months" / f"{year}-{month:02d}.json"
            saved = json.loads(path.read_bytes())
            if (saved["year"], saved["month"]) != (year, month):
                raise ValueError("MONTH_FILENAME_SCOPE_MISMATCH")
            replay = replay_month(saved, cache)
            months[path.name] = sec.file_sha(path)
            counts[year] += replay["document_hits"]
            pages += replay["raw_pages"]
    path = m.OUTPUT / "bounded-source-manifest.json"
    manifest = json.loads(path.read_bytes())
    binding = json.loads((m.OUTPUT / "bounded-source-manifest-binding.json").read_bytes())
    if binding["manifest_sha256"] != sec.file_sha(path):
        raise ValueError("BOUNDED_MANIFEST_BINDING_MISMATCH")
    verify_selection(manifest)
    for year, count in counts.items():
        if manifest["counts_by_year"][str(year)]["search_document_hits"] != count:
            raise ValueError("MANIFEST_YEAR_COUNT_MISMATCH")
    # A second offline index scan re-runs ranking and immutable-manifest comparison.
    replay_selection(manifest)
    result = {
        "status": "RAW_METADATA_REPLAY_AND_SOURCE_SELECTION_PASSED",
        "audit_registry": registry,
        "manifest_sha256": sec.file_sha(path),
        "month_file_sha256": months,
        "raw_pages_replayed": pages,
        "document_hits_by_year": dict(counts),
        "document_hits_total": sum(counts.values()),
        "selected_sources": 100,
        "query_hits_are_not_certified_events": True,
        "filing_body_downloads": 0,
        "market_queries": 0,
        "outcome_calculations": 0,
        "full_extraction_authorized": False,
        "phase3_authorized": False,
        "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
    }
    m.immutable(m.OUTPUT / "metadata-manifest-audit-v2.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "month_file_sha256"}))
    return result


if __name__ == "__main__":
    audit()
