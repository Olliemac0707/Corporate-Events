"""Offline adversarial checks for metadata evidence, not economic outcomes."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "discovery_audit_test", ROOT / "tools/corporate_events/sec_discovery_audit.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def test_all_registered_adapter_versions_preserve_source_bytes() -> None:
    assert m.verify_audit_registration()["version"] == "sec-discovery-audit-extension-v1"


def query() -> dict[str, Any]:
    return {
        "query": {
            "query": {
                "bool": {
                    "must": [],
                    "must_not": [],
                    "minimum_should_match": 1,
                    "should": [
                        {"match_phrase": {"doc_text": x}}
                        for x in ("repurchase", "buyback", "buy back")
                    ],
                }
            }
        }
    }


@pytest.mark.parametrize("change", ["must", "must_not", "minimum_should_match", "should"])
def test_query_terms_and_boolean_logic_cannot_silently_change(change: str) -> None:
    data = query()
    m.verify_query(data)
    data["query"]["query"]["bool"][change] = (
        [] if change in {"should", "minimum_should_match"} else ["unexpected"]
    )
    with pytest.raises(ValueError, match="SEMANTICS"):
        m.verify_query(data)


@pytest.mark.parametrize("defect", ["missing", "duplicate", "changed", "total"])
def test_replay_rejects_altered_month_even_if_complete_flag_true(
    monkeypatch: Any, defect: str
) -> None:
    row = {"search_hit_id": "a", "filed": "2006-03-01"}
    saved = {
        "year": 2006,
        "month": 3,
        "complete": True,
        "document_hits": 1,
        "records": [row.copy()],
        "raw_page_proofs": [{"offset": 0, "sort": "asc", "raw_sha256": "a" * 64}],
    }
    monkeypatch.setattr(m.m, "parse_page", lambda raw, payload: {"total": 1, "records": [row]})

    class Cache:
        def read(self, sha: str) -> bytes:
            return json.dumps(query()).encode()

    assert m.replay_month(saved, Cache())["document_hits"] == 1
    if defect == "missing":
        saved["records"] = []
    elif defect == "duplicate":
        saved["records"] = [row, row]
    elif defect == "changed":
        saved["records"] = [{**row, "filed": "2006-03-02"}]
    else:
        saved["document_hits"] = 2
    with pytest.raises(ValueError, match="RAW_"):
        m.replay_month(saved, Cache())


def test_wrong_source_count_cannot_pass() -> None:
    with pytest.raises(ValueError, match="QUOTAS"):
        m.verify_selection({"entries": []})
