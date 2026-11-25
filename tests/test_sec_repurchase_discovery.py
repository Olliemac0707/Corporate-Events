"""Prove pagination, scope and fixed ranking cannot silently produce a ready sample."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "paged_test", ROOT / "tools/corporate_events/sec_repurchase_discovery.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
c = m.client


def page() -> dict[str, Any]:
    return {
        "query": {
            "from": 0,
            "size": 100,
            "_source": {"exclude": ["doc_text"]},
            "query": {
                "bool": {
                    "filter": [
                        {"terms": {"root_forms": ["8-K"]}},
                        {"range": {"file_date": {"gte": "2006-01-01", "lte": "2006-01-31"}}},
                    ]
                }
            },
        },
        "hits": {
            "total": {"value": 1, "relation": "eq"},
            "hits": [
                {
                    "_id": "0000000123-06-000001:ex99.htm",
                    "_source": {
                        "file_date": "2006-01-10",
                        "form": "8-K",
                        "root_forms": ["8-K"],
                        "ciks": ["0000000123"],
                    },
                }
            ],
        },
    }


def test_root_filter_covers_amendments_and_metadata_only() -> None:
    data = page()
    data["hits"]["hits"][0]["_source"]["form"] = "8-K/A"
    data["hits"]["hits"][0]["highlight"] = {"text": ["body excerpt not used"]}
    result = m.parse_page(json.dumps(data).encode(), c.search_payload(2006, 1))
    assert result["total"] == 1 and result["records"][0]["search_form"] == "8-K/A"
    assert "body excerpt" not in json.dumps(result)


@pytest.mark.parametrize(
    "change",
    [
        "window",
        "gte",
        "wrong_size",
        "wrong_offset",
        "form",
        "date_filter",
        "date_hit",
        "text_exclusion",
        "body",
        "truncated",
        "timeout",
    ],
)
def test_page_validation_failures(change: str) -> None:
    d = page()
    if change == "window":
        d["hits"]["total"]["value"] = 10000
    if change == "gte":
        d["hits"]["total"]["relation"] = "gte"
    if change == "wrong_size":
        d["query"]["size"] = 10000
    if change == "wrong_offset":
        d["query"]["from"] = 100
    if change == "form":
        d["query"]["query"]["bool"]["filter"].append({"terms": {"form": ["8-K/A"]}})
    if change == "date_filter":
        d["query"]["query"]["bool"]["filter"][1]["range"]["file_date"]["gte"] = "2005-01-01"
    if change == "date_hit":
        d["hits"]["hits"][0]["_source"]["file_date"] = "2006-02-01"
    if change == "text_exclusion":
        d["query"]["_source"] = {}
    if change == "body":
        d["hits"]["hits"][0]["_source"]["doc_text"] = "unwanted body"
    if change == "truncated":
        d["hits"]["total"]["value"] = 2
    if change == "timeout":
        d["timed_out"] = True
    with pytest.raises(ValueError):
        m.parse_page(json.dumps(d).encode(), c.search_payload(2006, 1))


@pytest.mark.parametrize(
    "year,month,offset",
    [(2016, 1, 0), (2005, 1, 0), (2006, 0, 0), (2006, 13, 0), (2006, 1, 1), (2006, 1, 10000)],
)
def test_query_is_bounded(year: int, month: int, offset: int) -> None:
    with pytest.raises(ValueError):
        c.search_payload(year, month, offset)


@pytest.mark.parametrize(
    "key,value",
    [("forms", "8-K,8-K/A"), ("q", "returns"), ("ciks", "123"), ("size", 10000), ("from", True)],
)
def test_payload_cannot_add_filters_or_change_semantics(key: str, value: Any) -> None:
    p = c.search_payload(2006, 1)
    p[key] = value
    with pytest.raises(ValueError):
        c.validate_payload(p)


def test_calendar_month_and_leap_year() -> None:
    assert c.search_payload(2008, 2)["enddt"] == "2008-02-29"
    assert c.search_payload(2009, 2)["enddt"] == "2009-02-28"


def test_rank_is_independent_of_current_names_scores_and_outcomes() -> None:
    a = m.rank("f" * 64, 2006, "SOURCE_CONTROL", "edgar/data/1/1.txt")
    assert a == m.rank("f" * 64, 2006, "SOURCE_CONTROL", "edgar/data/1/1.txt")
    assert a != m.rank("f" * 64, 2006, "CANDIDATE_HIT", "edgar/data/1/1.txt")


def test_bounded_keeps_unique_accessions_and_stable_ties() -> None:
    rows = [
        {"accession": "a", "selection_rank": "01", "index_filename": "a.txt"},
        {"accession": "a", "selection_rank": "02", "index_filename": "a2.txt"},
        {"accession": "b", "selection_rank": "03", "index_filename": "b.txt"},
        {"accession": "c", "selection_rank": "04", "index_filename": "c.txt"},
    ]
    assert [x["accession"] for x in m.bounded(list(reversed(rows)), 2)] == ["a", "b"]


def test_complete_empty_partition_is_not_a_replacement() -> None:
    d = page()
    d["hits"] = {"total": {"value": 0, "relation": "eq"}, "hits": []}
    assert m.parse_page(json.dumps(d).encode(), c.search_payload(2006, 1))["records"] == []
