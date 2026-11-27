"""Prior-source metadata leads retain limited scope and never imply public time."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "prior_leads_test", ROOT / "tools/corporate_events/repurchase_prior_metadata_leads.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
ACC = "0000902664-07-002404"


def entry(accession: str, day: str, cik: str = "000123", filename: str = "x.htm") -> dict[str, Any]:
    return {
        "accession": accession,
        "filed": day,
        "search_ciks": [cik],
        "root_forms": ["8-K"],
        "search_hit_id": accession + ":" + filename,
    }


def case() -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {
            "case_key": "case",
            "accession": ACC,
            "stated_announcement_date": "2007-07-30",
            "source_filed_date": "2007-08-02",
        },
        {"case_key": "case", "accession": ACC, "reporting_filer_cik": "123"},
    )


def test_bounded_deduplicated_leads_never_claim_first_public() -> None:
    clock, action = case()
    prior = "0000000123-07-000001"
    results = m.select(
        clock,
        action,
        [
            entry(prior, "2007-07-30"),
            entry(prior, "2007-07-30", filename="y.htm"),
            entry(ACC, "2007-08-02"),
            entry("0000000123-07-000002", "2007-07-22"),
            entry("0000000123-07-000003", "2007-08-03"),
            entry("0000000123-07-000004", "2007-07-30", cik="999"),
        ],
    )
    assert results["metadata_candidate_accessions"] == 1
    assert len(results["candidates"][0]["search_hit_ids"]) == 2
    assert not results["absence_of_other_disclosure_proven"]
    assert not results["first_public_time_certified"]
    assert not results["candidates"][0]["source_contents_reviewed"]


def test_empty_search_does_not_prove_absence() -> None:
    clock, action = case()
    result = m.select(clock, action, [])
    assert not result["candidates"]
    assert not result["absence_of_other_disclosure_proven"]


def test_conflicting_dates_and_roles_rejected() -> None:
    clock, action = case()
    prior = "0000000123-07-000001"
    with pytest.raises(ValueError, match="CONFLICTING_FILED_DATE"):
        m.select(clock, action, [entry(prior, "2007-07-29"), entry(prior, "2007-07-30")])
    changed = entry(prior, "2007-07-29")
    changed["root_forms"] = ["10-K"]
    with pytest.raises(ValueError, match="ROOT_FORM"):
        m.select(clock, action, [changed])


def test_case_identity_mismatch_rejected() -> None:
    clock, action = case()
    action["accession"] = "0000000000-00-000000"
    with pytest.raises(ValueError, match="IDENTITY"):
        m.select(clock, action, [])
