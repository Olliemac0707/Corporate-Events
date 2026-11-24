"""Integrity failures and missing facts must never authorize research."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

spec = importlib.util.spec_from_file_location(
    "infrastructure_audit_tests",
    Path(__file__).resolve().parents[1] / "tools/corporate_events/infrastructure_audit.py",
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def rows() -> list[dict[str, Any]]:
    return [
        {
            "proposal_id": f"{i:064x}",
            "public_dissemination_certified": False,
            "independent_full_source_QA_complete": False,
            "research_event_accepted": False,
            "frozen_facts_unchanged": True,
            "required_evidence": ["INDEPENDENT_FACTUAL_QA", "PUBLIC_DISSEMINATION_EVIDENCE"],
        }
        for i in range(69)
    ]


@pytest.mark.parametrize(
    "name",
    ["../x", "/x", "C:/x", "C:x", "\\\\server\\x", "a\\b", "a//b", "./a", "a/../b", ""],
)
def test_manifest_path_cannot_escape_base(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError):
        m.safe_file(tmp_path, name)


def test_missing_artifact_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        m.verify_files(tmp_path, {"missing": "a" * 64})


def test_modified_artifact_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "artifact"
    path.write_bytes(b"original")
    expected = m.digest(path)
    assert m.verify_files(tmp_path, {"artifact": expected}) == 1
    path.write_bytes(b"modified")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        m.verify_files(tmp_path, {"artifact": expected})


@pytest.mark.parametrize("value", ["", "a" * 63, "A" * 64, "../private"])
def test_hash_syntax_enforced(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValueError, match="HASH_INVALID"):
        m.verify_files(tmp_path, {"file": value})


def test_empty_manifest_denied(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        m.verify_files(tmp_path, {})


def test_requirement_counts_do_not_open_gates() -> None:
    result = m.requirement_backlog(rows())
    assert result["unresolved_requirement_counts"]["INDEPENDENT_FACTUAL_QA"] == 69
    assert result["research_accepted_events"] == 0
    assert result["counts_are_requirements_not_unique_events_or_accuracy_metrics"]
    for key in ("full_extraction_authorized", "final_qa_authorized", "phase3_authorized"):
        assert result[key] is False


@pytest.mark.parametrize(
    "field",
    [
        "public_dissemination_certified",
        "independent_full_source_QA_complete",
        "research_event_accepted",
    ],
)
@pytest.mark.parametrize("value", [True, "false", 0, None])
def test_unregistered_gate_edits_rejected(field: str, value: object) -> None:
    cases = rows()
    cases[0][field] = value
    with pytest.raises(ValueError, match="UNRESOLVED_GATE_CHANGED"):
        m.requirement_backlog(cases)


def test_duplicate_candidate_not_hidden_by_count() -> None:
    cases = rows()
    cases[1] = copy.deepcopy(cases[0])
    with pytest.raises(ValueError, match="DUPLICATE"):
        m.requirement_backlog(cases)


def test_missing_candidate_is_not_a_smaller_successful_sample() -> None:
    with pytest.raises(ValueError, match="COUNT_CHANGED"):
        m.requirement_backlog(rows()[:-1])


def test_missing_requirement_not_silently_resolved() -> None:
    cases = rows()
    cases[0]["required_evidence"] = []
    with pytest.raises(ValueError, match="REQUIREMENTS_INVALID"):
        m.requirement_backlog(cases)


def test_frozen_fact_change_denied() -> None:
    cases = rows()
    cases[0]["frozen_facts_unchanged"] = False
    with pytest.raises(ValueError, match="FROZEN_FACTS"):
        m.requirement_backlog(cases)


@pytest.mark.parametrize("event", ["socket.connect", "socket.getaddrinfo", "http.client.connect"])
def test_audit_network_guard(event: str) -> None:
    with pytest.raises(RuntimeError, match="NETWORK_DENIED"):
        m.offline_guard(event, ())


@pytest.mark.parametrize("name", ["../overwrite", "offline-infrastructure-001/../../x", ""])
def test_unsafe_output_id_rejected_before_preflight(name: str) -> None:
    with pytest.raises(ValueError, match="RUN_ID_INVALID"):
        m.run(name)


def test_legacy_windows_snapshot_keys_verify_inside_base(tmp_path: Path) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    path = folder / "source"
    path.write_bytes(b"preserved")
    records = {"folder\\source": m.digest(path)}
    assert m.verify_files(tmp_path, m.legacy_snapshot(records)) == 1


@pytest.mark.parametrize("name", ["..\\outside", "\\\\server\\private", "C:\\private"])
def test_legacy_adapter_does_not_relax_boundary(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError):
        m.verify_files(tmp_path, m.legacy_snapshot({name: "a" * 64}))


def test_legacy_normalization_collision_denied() -> None:
    with pytest.raises(ValueError, match="COLLISION"):
        m.legacy_snapshot({"folder\\source": "a" * 64, "folder/source": "b" * 64})


def test_identical_legacy_aliases_are_one_verified_file() -> None:
    assert m.legacy_snapshot({"folder\\source": "a" * 64, "folder/source": "a" * 64}) == {
        "folder/source": "a" * 64
    }

