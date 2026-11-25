"""Test review priority, unbiased audit coverage and the no-auto-accept boundary."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "review_triage_test", ROOT / "tools/corporate_events/repurchase_review_triage.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
POLICY = {
    "priority_threshold": 35,
    "audit_seed": "a" * 64,
    "first_wave_priority_filing_cap": 6,
    "first_wave_lower_risk_audit_cap": 2,
    "first_wave_control_audit_cap": 2,
    "automatic_acceptance": False,
    "weights": {
        "PARSER_CONTEXT_SUBTYPE_DISAGREEMENT": 45,
        "CONTRACT_EXECUTION_VERSUS_AUTHORIZATION": 45,
        "EQUAL_CAPACITY_DISTINCT_ACTION_IDENTITY": 35,
        "MULTIPLE_PASSAGES_GROUPED": 10,
        "MULTIPLE_DATED_FACTS_IN_ACTION_PASSAGE": 25,
        "UNRESOLVED_OPERATIVE_COMMON_SHARE_APPROVAL": 45,
    },
}


def case_fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    case = {
        "case_key": "A",
        "accession": "filing-A",
        "members": ["R1"],
        "action_kind": "NEW_AUTHORIZATION",
        "temporal_scope": "CURRENT_RELEASE",
        "capacities": [{"value_decimal": "500000000", "unit": "DOLLARS"}],
        "facts": {"open_market_permission": "EXPLICIT", "approval_date": None},
        "support": {
            "action_identity": ["E1"],
            "capacity": ["E1"],
            "document_link": ["E2"],
            "stated_announcement_date": ["E1"],
        },
    }
    row = {
        "record_id": "R1",
        "accession": "filing-A",
        "component_id": "C1",
        "category": "CANDIDATE",
        "proposed_state": "NEW_AUTHORIZATION",
        "evidence": {
            "quote": "The Board authorized a share repurchase program.",
            "text_start": 0,
            "text_end": 50,
            "evidence_id": "EV1",
        },
    }
    return case, row


def test_zero_risk_has_no_probability_or_automatic_acceptance() -> None:
    case, row = case_fixture()
    scored = m.score_cases([case], [row], POLICY)[0]
    assert scored["risk_points"] == 0
    assert scored["correctness_probability"] is None
    assert scored["automatic_acceptance"] is False and scored["research_qualifying"] is False
    assert scored["calibration_status"] == "UNCALIBRATED_NO_INDEPENDENT_LABELS"


def test_shared_scientific_blockers_do_not_make_every_case_a_misinterpretation() -> None:
    case, row = case_fixture()
    case.update(
        first_public_dissemination_certified=False,
        independent_QA_passed=False,
        security_mapping_certified=False,
    )
    scored = m.score_cases([case], [row], POLICY)[0]
    assert scored["risk_points"] == 0 and scored["research_qualifying"] is False


def test_missing_venue_is_routed_to_evidence_work_without_inventing_it() -> None:
    case, row = case_fixture()
    case["facts"]["open_market_permission"] = "NOT_ESTABLISHED"
    result = m.score_cases([case], [row], POLICY)[0]
    assert "OPEN_MARKET_PERMISSION_EVIDENCE_REQUIRED" in result["source_evidence_followup"]
    assert result["automatic_acceptance"] is False


def test_subtype_disagreement_triggers_priority() -> None:
    case, row = case_fixture()
    case["action_kind"] = "AUTHORIZATION_INCREASE"
    result = m.score_cases([case], [row], POLICY)[0]
    assert result["risk_points"] == 45 and result["priority_review_flag"]
    assert result["flags"][0]["record_ids"] == ["R1"]


def test_correlated_asr_and_disagreement_flags_do_not_double_count() -> None:
    case, row = case_fixture()
    case.update(temporal_scope="NON_TARGET_ASR", action_kind="ASR_EXECUTION")
    result = m.score_cases([case], [row], POLICY)[0]
    assert len(result["flags"]) == 2 and result["risk_points"] == 45


def test_equal_amounts_in_distinct_actions_raise_programme_identity_risk() -> None:
    case, row = case_fixture()
    other = copy.deepcopy(case)
    other.update(case_key="B", members=[])
    other["capacities"][0]["value_decimal"] = "500000000.0"
    result = m.score_cases([case, other], [row], POLICY)
    assert all(x["risk_points"] == 35 and x["priority_review_flag"] for x in result)


def test_equal_amounts_in_different_filings_do_not_create_a_link() -> None:
    case, row = case_fixture()
    other = copy.deepcopy(case)
    other.update(case_key="B", members=[], accession="filing-B")
    assert m.score_cases([case, other], [row], POLICY)[0]["risk_points"] == 0


def test_grouping_and_multiple_date_roles_are_explained_separately() -> None:
    case, row = case_fixture()
    case["members"].append("R2")
    row["evidence"]["quote"] = (
        "On October 30, 2013, the Board authorized repurchases through June 30, 2014."
    )
    other = copy.deepcopy(row)
    other["record_id"] = "R2"
    scored = m.score_cases([case], [row, other], POLICY)[0]
    assert scored["risk_points"] == 35
    assert scored["risk_dimensions"] == {"GROUPING": 10, "DATE_ROLES": 25}


@pytest.mark.parametrize(
    "text,expected",
    [
        ("The Board approved a new common stock repurchase program.", True),
        ("In May 2009, the Board approved an additional common stock repurchase plan.", True),
        ("The Board may have approved a common stock repurchase program.", False),
        ("If conditions change, the Board approved a common stock repurchase plan.", False),
        ("The Board approved a repurchase of preferred stock.", False),
        ("The Board did not approve a common stock repurchase program.", False),
    ],
)
def test_near_misses_are_routed_without_turning_future_or_negated_actions_positive(
    text: str, expected: bool
) -> None:
    row = {"category": "UNRESOLVED", "evidence": {"quote": text}}
    assert m.near_miss(row) is expected


def test_points_are_bounded_and_monotone_across_independent_dimensions() -> None:
    flags = [
        {"dimension": "A", "points": 70},
        {"dimension": "A", "points": 50},
        {"dimension": "B", "points": 60},
    ]
    score, dimensions = m.points(flags)
    assert score == 100 and dimensions == {"A": 70, "B": 60}
    assert m.points(flags[:1])[0] <= score


@pytest.mark.parametrize("value", [-1, 101, True, 0.5])
def test_invalid_weights_fail(value: Any) -> None:
    with pytest.raises(ValueError, match="ORDINAL_SCALE"):
        m.points([{"dimension": "A", "points": value}])


def test_first_wave_is_bounded_reproducible_and_retains_backlog() -> None:
    units = []
    for index in range(8):
        units.append(
            {
                "accession": f"P{index}",
                "role": "CANDIDATE_HIT",
                "risk_points": 50,
                "priority_review_flag": True,
            }
        )
    for index in range(5):
        units.append(
            {
                "accession": f"L{index}",
                "role": "CANDIDATE_HIT",
                "risk_points": 0,
                "priority_review_flag": False,
            }
        )
    for index in range(20):
        units.append(
            {
                "accession": f"C{index}",
                "role": "SOURCE_CONTROL",
                "risk_points": 0,
                "priority_review_flag": False,
            }
        )
    before = copy.deepcopy(units)
    result = m.select_batch(units, POLICY)
    assert result == m.select_batch(list(reversed(units)), POLICY)
    assert len(result["selected"]) == 10 and len(result["priority_backlog"]) == 2
    assert len({x["accession"] for x in result["selected"]}) == 10
    assert result["lower_risk_audit_pool_size"] == 5 and result["control_audit_pool_size"] == 20
    assert result["is_population_accuracy_estimate"] is False and units == before


def test_source_packet_targets_late_flagged_text_and_withholds_predictions() -> None:
    text = "ordinary text " * 1000 + "IMPORTANT ORIGINAL SOURCE PASSAGE"
    start = text.index("IMPORTANT")
    rows = [
        {
            "record_id": "late",
            "accession": "A",
            "component_id": "C",
            "evidence": {"text_start": start, "text_end": len(text)},
        }
    ]
    unit = {
        "accession": "A",
        "flagged_record_ids": ["late"],
        "risk_points": 90,
        "selection_stratum": "PRIORITY_REVIEW",
    }
    components = [
        {
            "accession": "A",
            "component_id": "C",
            "filename": "release.htm",
            "derived_text_file": "derived-text/x.txt",
            "extraction_status": "VISIBLE_TEXT_EXTRACTED",
        }
    ]
    packet = m.source_packet(
        unit, rows, [], {}, components, POLICY["audit_seed"], lambda name: text
    )
    assert "IMPORTANT ORIGINAL SOURCE PASSAGE" in packet["excerpts"][0]["text"]
    raw = json.dumps(packet)
    for forbidden in ("risk_points", "selection_stratum", "priority_review_flag", "proposed_state"):
        assert forbidden not in raw
    assert packet["source_completeness_certified"] is False


def test_no_independent_labels_is_not_a_calibration_pass() -> None:
    report = m.calibration_status()
    assert report["status"] == "NOT_EVALUATED_NO_INDEPENDENT_LABELS"
    assert report["accuracy"] is None and report["precision"] is None and report["recall"] is None
    assert report["QA_gate_passed"] is False


@pytest.mark.parametrize(
    "key,value",
    [
        ("priority_threshold", 0),
        ("priority_threshold", 101),
        ("audit_seed", "bad"),
        ("automatic_acceptance", True),
        ("first_wave_priority_filing_cap", 20),
    ],
)
def test_policy_cannot_silently_expand_workload_or_acceptance(key: str, value: Any) -> None:
    policy = copy.deepcopy(POLICY)
    policy[key] = value
    with pytest.raises(ValueError):
        m.validate_policy(policy)
