"""Protect source-backed dates, capacity, duplicate links and the QA boundary."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "context_resolution_test", ROOT / "tools/corporate_events/repurchase_context_resolution.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def fixture() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    case = {
        "case_key": "A",
        "accession": "filing-A",
        "members": ["record-A"],
        "grouping_basis": "SINGLE_PASSAGE",
        "related_to": None,
        "temporal_scope": "CURRENT_RELEASE",
        "facts": {
            "approval_date": None,
            "stated_announcement_date": "2010-05-12",
            "effective_date": None,
            "common_share_scope": "EXPLICIT_COMMON",
            "open_market_permission": "EXPLICIT",
        },
        "capacities": [
            {
                "value_decimal": "50000000",
                "unit": "DOLLARS",
                "iso_currency": None,
                "derived": False,
                "evidence": ["E1"],
            }
        ],
        "support": {
            "action_identity": ["E1"],
            "stated_announcement_date": ["E1"],
            "common_share_scope": ["E1"],
            "open_market_permission": ["E1"],
            "capacity": ["E1"],
        },
    }
    records = {"record-A": {"accession": "filing-A"}}
    evidence = {
        "E1": {
            "accession": "filing-A",
            "quote": "On May 12, 2010, the Company announced "
            "that its Board approved a $50 million common stock repurchase program "
            "permitting open-market purchases.",
        }
    }
    return case, records, evidence


@pytest.mark.parametrize(
    "value,unit,text,expected",
    [
        ("6000000", "DOLLARS", "up to six million dollars", True),
        ("1000000", "SHARES", "up to 1,000,000 shares", True),
        ("25000000", "SHARES", "up to 25 million shares", True),
        ("2.5", "PERCENT_OUTSTANDING_SHARES", "an additional 2.5%", True),
        ("20000000", "DOLLARS", "$20 million increase", True),
        ("30000000", "DOLLARS", "$40 million capacity, $10 million purchased", False),
        ("50000000", "SHARES", "$50 million of common stock", False),
    ],
)
def test_capacity_is_normalized_from_wording_without_financial_derivation(
    value: str, unit: str, text: str, expected: bool
) -> None:
    assert m.amount_is_stated(value, unit, text) is expected


def test_source_scope_support_is_not_independent_qa_or_market_eligibility() -> None:
    case, records, evidence = fixture()
    result = m.validate_case(case, records, evidence)
    assert result["resolution_status"] == "CURRENT_RELEASE_SOURCE_SCOPE_SUPPORTED"
    for key in (
        "research_qualifying",
        "independent_QA_passed",
        "security_mapping_certified",
        "first_public_dissemination_certified",
        "deduplication_independently_certified",
    ):
        assert result[key] is False
    assert result["public_timestamp"] is None and result["return_entry_session"] is None


def test_announcement_day_is_not_an_approval_day() -> None:
    case, records, evidence = fixture()
    case["facts"].update(approval_date="2010-05-12", approval_date_basis="DIRECT_APPROVAL_DATE")
    case["support"]["approval_date"] = ["E1"]
    with pytest.raises(ValueError, match="ANNOUNCEMENT_DATE_CANNOT"):
        m.validate_case(case, records, evidence)


def test_filing_date_cannot_fill_an_unsupported_announcement_date() -> None:
    case, records, evidence = fixture()
    case["facts"]["stated_announcement_date"] = "2010-05-15"
    with pytest.raises(ValueError, match="DATE_NOT_PRESENT"):
        m.validate_case(case, records, evidence)


def test_effective_date_is_not_silently_an_approval_date() -> None:
    case, records, evidence = fixture()
    case["facts"]["effective_date"] = "2010-05-12"
    case["support"]["effective_date"] = ["E1"]
    with pytest.raises(ValueError, match="EFFECTIVE_DATE_ROLE"):
        m.validate_case(case, records, evidence)


def test_missing_open_market_evidence_leaves_scope_unresolved() -> None:
    case, records, evidence = fixture()
    case["facts"]["open_market_permission"] = "NOT_ESTABLISHED"
    result = m.validate_case(case, records, evidence)
    assert result["resolution_status"] == "CURRENT_RELEASE_SCOPE_UNRESOLVED"


def test_two_passages_need_a_documentary_link_to_be_grouped() -> None:
    case, records, evidence = fixture()
    records["record-B"] = {"accession": "filing-A"}
    case["members"].append("record-B")
    case["grouping_basis"] = "SAME_DATE_AND_AMOUNT"
    with pytest.raises(ValueError, match="MERGE_REQUIRES_DOCUMENTARY_LINK"):
        m.validate_case(case, records, evidence)
    case["grouping_basis"] = "EXPLICIT_DOCUMENT_LINK_AND_PROGRAMME_REFERENCE"
    case["support"].update(document_link=["E1"], programme_identity=["E1"])
    with pytest.raises(ValueError, match="DOCUMENTARY_LINK_WORDING"):
        m.validate_case(case, records, evidence)
    evidence["E1"]["quote"] += " The press release is attached as Exhibit 99.1."
    assert m.validate_case(case, records, evidence)["research_qualifying"] is False


def test_same_amounts_do_not_automatically_merge_distinct_programmes() -> None:
    case, records, _ = fixture()
    second = copy.deepcopy(case)
    second.update(case_key="B", members=["record-B"])
    records["record-B"] = {"accession": "filing-A"}
    m.validate_membership([case, second], records)
    assert len([case, second]) == 2


def test_shortlist_cannot_silently_drop_or_double_assign_a_passage() -> None:
    case, records, _ = fixture()
    m.validate_membership([case], records)
    with pytest.raises(ValueError, match="ASSIGNED_EXACTLY_ONCE"):
        m.validate_membership([], records)
    with pytest.raises(ValueError, match="ASSIGNED_EXACTLY_ONCE"):
        m.validate_membership([case, case], records)


def test_related_action_must_have_a_valid_context_relationship() -> None:
    case, records, evidence = fixture()
    child = copy.deepcopy(case)
    child.update(
        case_key="B", members=[], grouping_basis="RELATED_CONTEXT_DISTINCT_ACTION", related_to="A"
    )
    m.validate_membership([case, child], records)
    assert m.validate_case(child, records, evidence)["research_qualifying"] is False
    child["related_to"] = "unknown"
    with pytest.raises(ValueError, match="RELATIONSHIP_INVALID"):
        m.validate_membership([case, child], records)


def test_asr_contract_is_not_a_new_open_market_authorization() -> None:
    case, records, evidence = fixture()
    case["temporal_scope"] = "NON_TARGET_ASR"
    evidence["E1"]["quote"] += " This is an accelerated share repurchase agreement."
    assert (
        m.validate_case(case, records, evidence)["resolution_status"]
        == "NON_TARGET_ASR_EXECUTION_CONTEXT"
    )


def test_currency_code_is_not_inferred_from_a_dollar_symbol() -> None:
    case, records, evidence = fixture()
    case["capacities"][0]["iso_currency"] = "USD"
    with pytest.raises(ValueError, match="CURRENCY_CODE_INFERENCE"):
        m.validate_case(case, records, evidence)


def test_partial_approval_month_does_not_invent_a_day() -> None:
    case, records, evidence = fixture()
    case["temporal_scope"] = "OLDER_AUTHORIZATION"
    case["facts"]["approval_month"] = "2009-05"
    case["support"]["approval_month"] = ["E2"]
    evidence["E2"] = {
        "accession": "filing-A",
        "quote": "In May 2009, our Board approved an increase.",
    }
    result = m.validate_case(case, records, evidence)
    assert result["facts"]["approval_date"] is None
    assert result["facts"]["approval_month"] == "2009-05"
    case["facts"]["approval_month"] = "2009-06"
    with pytest.raises(ValueError, match="PARTIAL_MONTH"):
        m.validate_case(case, records, evidence)


def test_programme_relation_cannot_join_different_filings() -> None:
    case, records, _ = fixture()
    case["programme_relationship"] = {"other_case_key": "B", "type": "PREDECESSOR"}
    other = copy.deepcopy(case)
    other.update(case_key="B", accession="filing-B", members=["record-B"])
    other.pop("programme_relationship")
    records["record-B"] = {"accession": "filing-B"}
    with pytest.raises(ValueError, match="PROGRAMME_LINK_OUTSIDE"):
        m.validate_membership([case, other], records)
