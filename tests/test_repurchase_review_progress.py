"""Check scoped human feedback, preserved predictions and unresolved review work."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "progress_test", ROOT / "tools/corporate_events/repurchase_review_progress.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def fixture() -> tuple[Any, Any, Any, Any, Any]:
    cases = {
        "A": {"accession": "filing", "action_kind": "AUTHORIZATION_INCREASE", "members": ["R1"]}
    }
    target = {
        "question_id": "T1",
        "case_keys": ["A"],
        "issue_type": "SUBTYPE",
        "fact_reads": {"action_kind": {"case_key": "A", "path": ["action_kind"]}},
        "choice_values": {
            "A": {"action_kind": "AUTHORIZATION_INCREASE"},
            "B": {"action_kind": "NEW_AUTHORIZATION"},
            "C": None,
        },
        "required_display_text": ["approved an increase"],
        "resolved_flag_codes": ["SUBTYPE_CONFLICT"],
    }
    decision = {
        "question_id": "T1",
        "accession": "filing",
        "choice": "A",
        "source_spans": [{"text": "The Board approved an increase."}],
        "independent_gold_label": False,
    }
    flags = [
        {"code": "SUBTYPE_CONFLICT", "dimension": "SEMANTIC", "points": 45},
        {"code": "GROUPED", "dimension": "GROUPING", "points": 10},
    ]
    case_score = {"accession": "filing", "case_key": "A", "flags": flags}
    passage = {"accession": "filing", "record_id": "R1", "flags": copy.deepcopy(flags)}
    return cases, target, decision, case_score, passage


def queue(cases: Any, comparisons: Any, scores: Any, passages: Any) -> Any:
    return m.remaining_queue(
        [{"accession": "filing", "risk_points": 55, "priority_review_flag": True}],
        scores,
        passages,
        cases,
        comparisons,
        35,
    )[0]


def test_agreement_resolves_only_the_checked_flag_and_preserves_original() -> None:
    cases, target, decision, score, passage = fixture()
    original = copy.deepcopy([cases, target, decision, score, passage])
    observations = m.compare([target], [decision], cases)
    result = queue(cases, observations, [score], [passage])
    assert result["original_risk_points"] == 55
    assert result["remaining_review_priority_points"] == 10
    assert result["remaining_flag_codes"] == ["GROUPED"]
    assert result["correctness_probability"] is None
    assert not result["fully_reviewed"] and not result["automatic_acceptance"]
    assert not result["research_qualifying"]
    assert [cases, target, decision, score, passage] == original


@pytest.mark.parametrize("choice,status", [("B", "DISAGREES_WITH_CONTEXT"), ("C", "UNRESOLVED")])
def test_disagreement_or_uncertainty_cannot_reduce_priority(choice: str, status: str) -> None:
    cases, target, decision, score, passage = fixture()
    decision["choice"] = choice
    observations = m.compare([target], [decision], cases)
    result = queue(cases, observations, [score], [passage])
    assert observations[0]["status"] == status
    assert result["remaining_review_priority_points"] == 55
    assert result["adjudication_required"] and result["remaining_priority_flag"]


def test_another_unreviewed_case_in_the_filing_keeps_its_priority() -> None:
    cases, target, decision, score, passage = fixture()
    cases["B"] = {"accession": "filing", "members": ["R2"]}
    other = {
        "accession": "filing",
        "case_key": "B",
        "flags": [{"code": "OTHER_CONFLICT", "dimension": "SEMANTIC", "points": 45}],
    }
    observations = m.compare([target], [decision], cases)
    result = queue(cases, observations, [score, other], [passage])
    assert result["remaining_review_priority_points"] == 45
    assert result["remaining_priority_flag"]


def test_unreviewed_zero_risk_is_still_unvalidated() -> None:
    result = m.remaining_queue(
        [{"accession": "zero", "risk_points": 0, "priority_review_flag": False}],
        [],
        [],
        {},
        [],
        35,
    )[0]
    assert result["human_confirmed_questions"] == 0
    assert not result["fully_reviewed"] and not result["research_qualifying"]


@pytest.mark.parametrize(
    "change",
    ["duplicate", "gold", "wrong_source", "unshown_text", "missing_fact", "invalid_choice"],
)
def test_invalid_feedback_is_rejected(change: str) -> None:
    cases, target, decision, _, _ = fixture()
    decisions = [decision]
    if change == "duplicate":
        decisions.append(copy.deepcopy(decision))
    elif change == "gold":
        decision["independent_gold_label"] = True
    elif change == "wrong_source":
        decision["accession"] = "another-filing"
    elif change == "unshown_text":
        decision["source_spans"] = [{"text": "No corresponding assertion shown."}]
    elif change == "missing_fact":
        target["choice_values"]["A"] = {}
    else:
        decision["choice"] = "X"
    with pytest.raises(ValueError):
        m.compare([target], decisions, cases)


def test_a_confirmed_later_action_does_not_mark_the_earlier_action_as_wrong() -> None:
    cases, target, decision, _, _ = fixture()
    cases["OLD"] = {"accession": "filing", "members": ["R0"]}
    cases["A"]["members"] = []
    target["case_keys"].append("OLD")
    observations = m.compare([target], [decision], cases)
    rows = {"R0": {"proposed_state": "NEW_AUTHORIZATION"}}
    assert m.baseline_conflicts(observations, cases, rows) == []


def test_original_parser_disagreement_survives_agreement_with_context() -> None:
    cases, target, decision, _, _ = fixture()
    observations = m.compare([target], [decision], cases)
    rows = {"R1": {"proposed_state": "NEW_AUTHORIZATION"}}
    conflicts = m.baseline_conflicts(observations, cases, rows)
    assert conflicts[0]["original_parser_disagreeing_record_ids"] == ["R1"]
    assert observations[0]["status"] == "AGREES_WITH_CONTEXT"
