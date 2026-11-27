"""Ensure filing-date and archive observations never become public timing facts."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "five_clock_test", ROOT / "tools/corporate_events/repurchase_five_clock_inventory.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
ACC = "0000902664-07-002404"


def row(announced: str = "2007-07-30", archive_day: str = "2007-08-02") -> dict[str, Any]:
    action = {
        "accession": ACC,
        "case_key": "test",
        "company": "example",
        "action_kind": "NEW_AUTHORIZATION",
        "facts": {"stated_announcement_date": announced},
        "source_filed": "2007-08-02",
    }
    archive = {"target_accessions": [ACC], "archive_date_label": archive_day}
    member = {
        "accession": ACC,
        "member_sha256": "a" * 64,
        "internal_accession_verified": True,
        "full_gzip_checksum_verified": False,
        "component_comparison": {"nonmatching_components_require_review": False},
    }
    result: dict[str, Any] = m.one_row(action, archive, member, "20070802153846")
    return result


def test_dates_are_inventory_not_public_time() -> None:
    result = row()
    assert result["stated_date_precedes_archive_label"]
    assert result["filing_date_matches_archive_label"]
    assert not result["archive_label_is_dissemination_day_proof"]
    assert result["registered_public_datetime"] is None
    assert result["classification_available_at"] is None
    assert not result["model_row_eligible"]
    assert not result["full_gzip_checksum_verified"]


def test_equal_dates_are_not_assumed_first_public() -> None:
    result = row("2007-08-02")
    assert not result["stated_date_precedes_archive_label"]
    assert not result["first_public_disclosure_verified"]
    assert result["public_time_status"] == "UNRESOLVED"


@pytest.mark.parametrize("bad", ["bad", "2007-02-30", "20070802"])
def test_invalid_or_ambiguous_dates_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="DATE_INVALID"):
        row(bad)


def test_mismatched_membership_does_not_certify() -> None:
    action = {
        "accession": ACC,
        "case_key": "test",
        "company": "example",
        "action_kind": "NEW_AUTHORIZATION",
        "facts": {"stated_announcement_date": "2007-07-30"},
        "source_filed": "2007-08-02",
    }
    with pytest.raises(ValueError, match="IDENTITY"):
        m.one_row(action, {"target_accessions": []}, {"accession": ACC}, "20070802153846")
