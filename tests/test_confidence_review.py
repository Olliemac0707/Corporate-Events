"""Synthetic regressions for confidence-based AI development review routing."""

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
    "confidence_review_test", ROOT / "tools/corporate_events/confidence_review.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def fixture() -> tuple[dict[str, Any], dict[str, Any], bytes, dict[str, Any]]:
    policy = json.loads((ROOT / m.POLICY_FILE).read_bytes())
    source = b"Synthetic source with explicit board authorization."
    case = {
        "sample_record_id": "case1",
        "source_id": "source1",
        "family": "SHARE_REPURCHASE",
        "role": "INDEPENDENT_SOURCE_CONTROL",
        "source_parse_status": "TEXT_SUPPORTED",
        "source_document_sha256": m.digest(source),
    }
    item = {
        "sample_record_id": "case1",
        "reviewer_kind": "AI",
        "reviewer_id": policy["ai_reviewer_id"],
        "model_id": None,
        "finding": "Synthetic assessment fixture",
        "classification_state": "NEW_AUTHORIZATION",
        "confidence": {"classification": 95, "evidence": 95},
        "coverage": "FULL_SOURCE_REVIEWED",
        "source_role_status": "RESOLVED",
        "essential_checks": {
            key: {"status": "RESOLVED", "rationale": "Synthetic supporting evidence"}
            for key in policy["essential_checks"]["SHARE_REPURCHASE"]
        },
        "evidence_spans": [{"byte_start": 0, "byte_end": len(source), "sha256": m.digest(source)}],
        "flags": [],
        "rationale": "Synthetic fixture only",
        "supersedes_assessment_id": None,
    }
    return policy, case, source, item


@pytest.mark.parametrize(
    "score,route", [(89, "MANUAL_REVIEW_REQUIRED"), (90, "AI_REVIEWED_DEVELOPMENT")]
)
def test_threshold_boundary(score: int, route: str) -> None:
    policy, case, source, item = fixture()
    item["confidence"]["classification"] = score
    result = m.validate_and_route(item, case, source, policy, m.admin.labels())
    assert result["route"] == route and result["overall_score"] == score
    assert result["timing_status"] == "TIMESTAMP_UNRESOLVED"
    assert result["timing_confidence"] is None


def test_minimum_dimension_determines_route_and_input_is_preserved() -> None:
    policy, case, source, item = fixture()
    item["confidence"] = {"classification": 100, "evidence": 85}
    before = copy.deepcopy(item)
    result = m.validate_and_route(item, case, source, policy, m.admin.labels())
    assert result["overall_score"] == 85 and result["route"] == "MANUAL_REVIEW_REQUIRED"
    assert item == before


@pytest.mark.parametrize(
    "condition",
    [
        "partial",
        "unsupported",
        "unresolved_state",
        "unresolved_role",
        "missing_fact",
        "conflicting_fact",
        "missing_evidence",
        "explicit_flag",
        "all_checks_skipped",
    ],
)
def test_high_scores_cannot_override_critical_issues(condition: str) -> None:
    policy, case, source, item = fixture()
    if condition == "partial":
        item["coverage"] = "PARTIAL_SOURCE_REVIEWED"
    elif condition == "unsupported":
        case["source_parse_status"] = "UNSUPPORTED_COMPONENT_RETAINED"
    elif condition == "unresolved_state":
        item["classification_state"] = "UNRESOLVED"
    elif condition == "unresolved_role":
        item["source_role_status"] = "UNRESOLVED"
    elif condition == "missing_fact":
        item["essential_checks"]["action_type"]["status"] = "UNRESOLVED"
    elif condition == "conflicting_fact":
        item["essential_checks"]["action_type"]["status"] = "CONFLICTING"
    elif condition == "missing_evidence":
        item["evidence_spans"] = []
    elif condition == "explicit_flag":
        item["flags"] = ["BASELINE_UNRESOLVED"]
    else:
        for check in item["essential_checks"].values():
            check["status"] = "NOT_APPLICABLE"
    result = m.validate_and_route(item, case, source, policy, m.admin.labels())
    assert result["route"] == "MANUAL_REVIEW_REQUIRED" and result["manual_reasons"]


@pytest.mark.parametrize(
    "invalid",
    [
        "unknown_field",
        "human_identity",
        "bad_score",
        "boolean_score",
        "bad_citation",
        "negative_offset",
        "wrong_source",
        "missing_check",
        "wrong_family_state",
        "invented_flag",
    ],
)
def test_invalid_assessments_are_rejected(invalid: str) -> None:
    policy, case, source, item = fixture()
    if invalid == "unknown_field":
        item["CAR"] = 0
    elif invalid == "human_identity":
        item["reviewer_kind"] = "HUMAN"
    elif invalid == "bad_score":
        item["confidence"]["evidence"] = 101
    elif invalid == "boolean_score":
        item["confidence"]["evidence"] = True
    elif invalid == "bad_citation":
        item["evidence_spans"][0]["sha256"] = "0" * 64
    elif invalid == "negative_offset":
        item["evidence_spans"][0]["byte_start"] = -1
    elif invalid == "wrong_source":
        source += b"changed"
    elif invalid == "missing_check":
        item["essential_checks"].pop("action_type")
    elif invalid == "wrong_family_state":
        item["classification_state"] = "SPECIAL_DIVIDEND"
    else:
        item["flags"] = ["AUTO_APPROVE"]
    with pytest.raises(ValueError):
        m.validate_and_route(item, case, source, policy, m.admin.labels())


def synthetic_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    policy, case, source, item = fixture()
    monkeypatch.setattr(m, "OUTPUT", tmp_path / "ai-review")
    monkeypatch.setattr(m, "RUN", tmp_path / "original-run")
    frame = {
        "sample_sha256": m.admin.EXPECTED_SHA,
        "policy_sha256": "policy1",
        "policy": policy,
        "records": [case],
    }
    m.exclusive(m.OUTPUT / "frame.json", frame)
    m.workflow.sec.write_new(m.OUTPUT / "ai-assessments.jsonl", b"")
    m.workflow.sec.write_new(m.RUN / "documents" / (m.digest(source) + ".txt"), source)
    m.exclusive(
        m.RUN / "run-manifest.json",
        {"original_component_hashes": {m.digest(source) + ".txt": m.digest(source)}},
    )
    m.exclusive(m.OUTPUT / "inbox/synthetic.json", item)
    return frame, {"code_commit": "synthetic", "confidence_code_hashes": {}}, item


def test_recording_is_idempotent_and_never_counts_ai_as_human(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame, proof, _ = synthetic_store(tmp_path, monkeypatch)
    m.record("synthetic.json", frame, proof)
    before = (m.OUTPUT / "ai-assessments.jsonl").read_bytes()
    assert (
        m.record("synthetic.json", frame, proof)["status"]
        == "IDENTICAL_AI_ASSESSMENT_ALREADY_RECORDED"
    )
    assert (m.OUTPUT / "ai-assessments.jsonl").read_bytes() == before
    result = m.status(frame)
    assert result["ai_assessed"] == 1 and result["human_review_records"] == 0
    assert result["human_accuracy"] == "NOT_MEASURED"
    assert not result["full_extraction_authorized"]


def test_unassessed_cases_are_pending_without_invented_confidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame, _, _ = synthetic_store(tmp_path, monkeypatch)
    result = m.status(frame)
    assert result["ai_review_pending"] == 1 and result["ai_assessed"] == 0
    assert result["manual_review_records"] == result["above_threshold_clear"] == 0


def test_candidate_assessment_requires_prior_ai_control_assessments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame, proof, item = synthetic_store(tmp_path, monkeypatch)
    candidate = {**frame["records"][0], "sample_record_id": "candidate", "role": "PARSER_CANDIDATE"}
    frame["records"].append(candidate)
    item["sample_record_id"] = "candidate"
    m.exclusive(m.OUTPUT / "inbox/candidate.json", item)
    with pytest.raises(ValueError, match="CONTROL_FIRST"):
        m.record("candidate.json", frame, proof)
    assert (m.OUTPUT / "ai-assessments.jsonl").read_bytes() == b""


def test_supersession_preserves_prior_assessment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame, proof, item = synthetic_store(tmp_path, monkeypatch)
    first = m.record("synthetic.json", frame, proof)
    before = (m.OUTPUT / "ai-assessments.jsonl").read_bytes()
    item["confidence"]["classification"] = 75
    item["supersedes_assessment_id"] = first["assessment_id"]
    m.exclusive(m.OUTPUT / "inbox/correction.json", item)
    m.record("correction.json", frame, proof)
    assert (m.OUTPUT / "ai-assessments.jsonl").read_bytes().startswith(before)
    result = m.status(frame)
    assert result["ai_assessed"] == 1 and result["manual_review_records"] == 1


def test_journal_tampering_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frame, proof, _ = synthetic_store(tmp_path, monkeypatch)
    m.record("synthetic.json", frame, proof)
    path = m.OUTPUT / "ai-assessments.jsonl"
    item = json.loads(path.read_bytes())
    item["routing"]["overall_score"] = 100
    path.write_bytes(m.canonical(item))
    with pytest.raises(ValueError, match="CHAIN"):
        m.status(frame)


@pytest.mark.parametrize(
    "family,state",
    [
        ("SHARE_REPURCHASE", "AMBIGUOUS"),
        ("ACTIVIST_13D", "INTENT_UNRESOLVED"),
        ("DIVIDEND_POSITIVE_CHANGE", "UNKNOWN_BASELINE"),
        ("INSIDER_OPEN_MARKET_PURCHASE", "VENUE_UNRESOLVED"),
        ("INSIDER_OPEN_MARKET_PURCHASE", "DISCRETION_UNRESOLVED"),
        ("INSIDER_OPEN_MARKET_PURCHASE", "SECURITY_CLASS_UNRESOLVED"),
    ],
)
def test_family_unresolved_states_always_require_manual_review(family: str, state: str) -> None:
    policy, case, source, item = fixture()
    case["family"] = family
    item["classification_state"] = state
    item["essential_checks"] = {
        key: {"status": "RESOLVED", "rationale": "Synthetic check"}
        for key in policy["essential_checks"][family]
    }
    result = m.validate_and_route(item, case, source, policy, m.admin.labels())
    assert result["overall_score"] == 95
    assert result["route"] == "MANUAL_REVIEW_REQUIRED"
    assert "CLASSIFICATION_UNRESOLVED" in result["manual_reasons"]


def test_pending_controls_follow_the_fixed_review_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame, proof, item = synthetic_store(tmp_path, monkeypatch)
    frame["records"].append({**frame["records"][0], "sample_record_id": "second"})
    item["sample_record_id"] = "second"
    m.exclusive(m.OUTPUT / "inbox/second.json", item)
    with pytest.raises(ValueError, match="FROZEN_REVIEW_ORDER"):
        m.record("second.json", frame, proof)
    assert (m.OUTPUT / "ai-assessments.jsonl").read_bytes() == b""
