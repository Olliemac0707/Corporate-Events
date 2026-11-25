"""Export scope, truncation, response and cross-restart budget validation."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "export_test", ROOT / "tools/corporate_events/sec_search_export.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
c = m.client


def sample() -> dict[str, Any]:
    return {
        "hits": {
            "total": {"value": 1, "relation": "eq"},
            "hits": [
                {
                    "_id": "0000000123-06-000001:ex99.htm",
                    "_source": {
                        "file_date": "2006-04-01",
                        "form": "8-K",
                        "root_forms": ["8-K"],
                        "ciks": ["0000000123"],
                    },
                }
            ],
        }
    }


def test_complete_export_excludes_nonmetadata_fields() -> None:
    d = sample()
    d["hits"]["hits"][0]["highlight"] = {"text": ["IGNORE ME"]}
    result = m.metadata(json.dumps(d).encode(), 2006)
    assert result["complete_export"] and result["distinct_accessions"] == 1
    assert result["records"][0]["search_ciks"] == ["123"]
    assert "IGNORE ME" not in json.dumps(result)


@pytest.mark.parametrize(
    "change",
    [
        "truncated",
        "gte",
        "duplicate",
        "outside_date",
        "outside_form",
        "missing_cik",
        "timeout",
        "shard",
    ],
)
def test_bad_exports_fail_closed(change: str) -> None:
    d = sample()
    if change == "truncated":
        d["hits"]["total"]["value"] = 2
    if change == "gte":
        d["hits"]["total"]["relation"] = "gte"
    if change == "duplicate":
        d["hits"]["hits"] *= 2
        d["hits"]["total"]["value"] = 2
    if change == "outside_date":
        d["hits"]["hits"][0]["_source"]["file_date"] = "2016-01-01"
    if change == "outside_form":
        d["hits"]["hits"][0]["_source"].update(form="10-K", root_forms=["10-K"])
    if change == "missing_cik":
        d["hits"]["hits"][0]["_source"]["ciks"] = []
    if change == "timeout":
        d["timed_out"] = True
    if change == "shard":
        d["_shards"] = {"failed": 1}
    with pytest.raises(ValueError):
        m.metadata(json.dumps(d).encode(), 2006)


def test_empty_export_is_explicit_not_filled() -> None:
    result = m.metadata(b'{"hits":{"total":{"value":0,"relation":"eq"},"hits":[]}}', 2006)
    assert result["complete_export"] and not result["records"]


@pytest.mark.parametrize("year", [2005, 2016, True, 2006.0])
def test_year_scope(year: Any) -> None:
    with pytest.raises(ValueError):
        c.search_payload(year)


@pytest.mark.parametrize(
    "key,value",
    [
        ("size", 10001),
        ("from", 100),
        ("q", "price"),
        ("ciks", "123"),
        ("forms", "10-K"),
        ("narrow", "highlights"),
        ("enddt", "2016-01-01"),
    ],
)
def test_query_scope_is_closed(key: str, value: Any) -> None:
    p = c.search_payload(2006)
    p[key] = value
    with pytest.raises(ValueError):
        c.validate_request(c.SEARCH, "GET", p)


def test_observed_get_route_and_canonical_payload() -> None:
    c.validate_request(c.SEARCH, "GET", c.search_payload(2006))
    with pytest.raises(ValueError):
        c.validate_request(c.SEARCH, "POST", c.search_payload(2006))
    with pytest.raises(ValueError):
        c.validate_request(c.SEARCH + "?q=x", "GET", c.search_payload(2006))


def test_compact_accession_is_normalized_without_guessing() -> None:
    assert m.accession("000000012306000001") == "0000000123-06-000001"
    with pytest.raises(ValueError):
        m.accession("../../bad")


def test_combined_budget_keeps_bootstrap_requests(tmp_path: Path) -> None:
    (tmp_path / "diagnostic-budget.json").write_text(json.dumps({"requests": 12}))

    class Scheduler:
        def attempts_used(self, key: str) -> int:
            return 0

        def start(self, deadline: float) -> float:
            raise AssertionError("must not request")

    obj = c.Client(m.sec, tmp_path, Scheduler(), "private", "exports", 10, 134217728)
    with pytest.raises(ValueError, match="TWELVE"):
        obj.fetch(c.SEARCH, "GET", c.search_payload(2006))
