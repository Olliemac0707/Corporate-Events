"""Historical index leads retain source, date and issuer-role limitations."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "prior_index_test", ROOT / "tools/corporate_events/repurchase_prior_index_leads.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
ACC = "0000902664-07-002404"


def entry(path: str, day: str, cik: str = "000123", form: str = "8-K") -> dict[str, Any]:
    return {"filename": path, "filed": day, "cik": cik, "form": form, "company": "example"}


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


def test_window_excludes_self_and_never_certifies_publicity() -> None:
    clock, action = case()
    path = "edgar/data/123/0000000123-07-000001.txt"
    result = m.select(
        clock,
        action,
        [
            entry(path, "2007-07-30"),
            entry(path, "2007-07-30"),
            entry("edgar/data/123/" + ACC + ".txt", "2007-08-02"),
            entry("too-early", "2007-07-22"),
            entry("too-late", "2007-08-03"),
            entry("different", "2007-07-30", cik="999"),
        ],
    )
    assert result["metadata_candidate_rows"] == 1
    assert not result["first_public_time_certified"]
    assert not result["absence_of_other_disclosure_proven"]
    assert not result["candidates"][0]["source_contents_reviewed"]


def test_all_form_types_are_preserved_as_unreviewed_leads() -> None:
    clock, action = case()
    result = m.select(
        clock,
        action,
        [entry("same", "2007-07-30", form="10-Q"), entry("same", "2007-07-30", form="8-K")],
    )
    assert {x["form"] for x in result["candidates"]} == {"8-K", "10-Q"}


def test_empty_index_result_does_not_prove_absence() -> None:
    clock, action = case()
    result = m.select(clock, action, [])
    assert not result["candidates"]
    assert not result["absence_of_other_disclosure_proven"]


def test_identity_mismatch_rejected() -> None:
    clock, action = case()
    action["accession"] = "0000000000-00-000000"
    with pytest.raises(ValueError, match="IDENTITY"):
        m.select(clock, action, [])
