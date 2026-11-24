"""Offline runner gates and evidence binding regression checks."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "cea_v3_runner_test", ROOT / "tools/corporate_events/parser_development_v3.py"
)
assert spec and spec.loader
r: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = r
spec.loader.exec_module(r)


def policy() -> dict[str, Any]:
    return {
        "version": "parser-development-v3",
        "sample_sha256": r.review.admin.EXPECTED_SHA,
        "sample_records": 718,
        "sample_cells": 297,
        "authorization": "USER_RESUMED_STRUCTURAL_FACTUAL_CORRECTIONS_AFTER_V2_QA",
        **dict.fromkeys(
            (
                "network_allowed",
                "market_data_allowed",
                "new_filings_allowed",
                "final_qa_authorized",
                "full_extraction_authorized",
                "phase3_authorized",
                "baseline_overwrite_allowed",
                "sample_redraw_allowed",
            ),
            False,
        ),
    }


def test_registered_offline_policy() -> None:
    r.check_policy(policy())


@pytest.mark.parametrize(
    "key",
    [
        "network_allowed",
        "market_data_allowed",
        "new_filings_allowed",
        "final_qa_authorized",
        "full_extraction_authorized",
        "phase3_authorized",
        "baseline_overwrite_allowed",
        "sample_redraw_allowed",
    ],
)
def test_closed_gates_cannot_be_relaxed(key: str) -> None:
    value = policy()
    value[key] = True
    with pytest.raises(ValueError, match="CLOSED_GATE"):
        r.check_policy(value)


@pytest.mark.parametrize(
    "key,value", [("sample_records", 717), ("sample_cells", 296), ("sample_sha256", "changed")]
)
def test_original_frame_is_required(key: str, value: object) -> None:
    changed = policy()
    changed[key] = value
    with pytest.raises(ValueError):
        r.check_policy(changed)


@pytest.mark.parametrize("event", ["socket.connect", "socket.getaddrinfo", "http.client.connect"])
def test_offline_hook_denies_network_before_any_request(event: str) -> None:
    with pytest.raises(RuntimeError, match="OFFLINE_NETWORK_FORBIDDEN"):
        r.offline_audit(event, ())


@pytest.mark.parametrize(
    "run_id", ["../old-run", "development-parse-v2", "offline-development-v3-001/other"]
)
def test_run_id_cannot_target_original_outputs(run_id: str) -> None:
    with pytest.raises(ValueError, match="RUN_ID_INVALID"):
        r.run(run_id)


def test_evidence_hash_range_and_certification_fail_closed() -> None:
    raw = b"The Board approved a new $15 million common stock repurchase program."
    source = r.parser.base.Document(
        "0000000123-24-000001", "body.htm", "8-K", "BODY", "1", 0, len(raw), raw
    )
    result = r.parser.narrative(source, "8-K", "123")
    assert r.verify_evidence(source, result) >= 1
    result["observations"][0]["evidence"]["text_start"] = -1
    with pytest.raises(ValueError, match="EVIDENCE_RANGE"):
        r.verify_evidence(source, result)


def test_autocertification_is_forbidden() -> None:
    raw = b"The Board approved a new $15 million common stock repurchase program."
    source = r.parser.base.Document(
        "0000000123-24-000001", "body.htm", "8-K", "BODY", "1", 0, len(raw), raw
    )
    result = r.parser.narrative(source, "8-K", "123")
    result["observations"][0]["certified_qualifying"] = True
    with pytest.raises(ValueError, match="AUTOMATIC_CERTIFICATION"):
        r.verify_evidence(source, result)


def test_frozen_frame_order_roles_and_policy_cannot_be_replaced(monkeypatch: Any) -> None:
    frozen = [
        {"source_id": "one", "family": "SHARE_REPURCHASE", "role": "INDEPENDENT_SOURCE_CONTROL"}
    ]
    monkeypatch.setattr(r.review.admin, "batches", lambda qa: [dict(x) for x in frozen])
    qa = {"selected": [{"source_id": "one", "parse_status": "UNRESOLVED"}]}
    confidence = {"threshold": 90}
    proof = {"policy_sha256": "policy-hash"}
    expected = [{**frozen[0], "source_parse_status": "UNRESOLVED"}]
    frame = {
        "records": expected,
        "sample_sha256": r.review.admin.EXPECTED_SHA,
        "policy": confidence,
        "policy_sha256": "policy-hash",
    }
    r.validate_frame(frame, qa, confidence, proof)
    for changed in ({**frame, "records": []}, {**frame, "policy": {"threshold": 0}}):
        with pytest.raises(ValueError, match="FRAME_DOES_NOT_MATCH"):
            r.validate_frame(changed, qa, confidence, proof)
