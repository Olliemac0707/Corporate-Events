"""Offline adversarial checks for metadata evidence, not economic outcomes."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "discovery_audit_v2_test", ROOT / "tools/corporate_events/sec_discovery_audit_v2.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def test_all_registered_adapter_versions_preserve_source_bytes() -> None:
    assert m.verify_audit_registration()["version"] == "sec-discovery-audit-extension-v2"


def query() -> dict[str, Any]:
    return {
        "query": {
            "query": {
                "bool": {
                    "must": [],
                    "must_not": [],
                    "minimum_should_match": 1,
                    "should": [
                        {"match_phrase": {"doc_text": x}}
                        for x in ("repurchase", "buyback", "buy back")
                    ],
                }
            }
        }
    }


@pytest.mark.parametrize("change", ["must", "must_not", "minimum_should_match", "should"])
def test_query_terms_and_boolean_logic_cannot_silently_change(change: str) -> None:
    data = query()
    m.verify_query(data)
    data["query"]["query"]["bool"][change] = (
        [] if change in {"should", "minimum_should_match"} else ["unexpected"]
    )
    with pytest.raises(ValueError, match="SEMANTICS"):
        m.verify_query(data)


@pytest.mark.parametrize("defect", ["missing", "duplicate", "changed", "total"])
def test_replay_rejects_altered_month_even_if_complete_flag_true(
    monkeypatch: Any, defect: str
) -> None:
    row = {"search_hit_id": "a", "filed": "2006-03-01"}
    saved = {
        "year": 2006,
        "month": 3,
        "complete": True,
        "document_hits": 1,
        "records": [row.copy()],
        "raw_page_proofs": [{"offset": 0, "sort": "asc", "raw_sha256": "a" * 64}],
    }
    monkeypatch.setattr(m.m, "parse_page", lambda raw, payload: {"total": 1, "records": [row]})

    class Cache:
        def read(self, sha: str) -> bytes:
            return json.dumps(query()).encode()

    assert m.replay_month(saved, Cache())["document_hits"] == 1
    if defect == "missing":
        saved["records"] = []
    elif defect == "duplicate":
        saved["records"] = [row, row]
    elif defect == "changed":
        saved["records"] = [{**row, "filed": "2006-03-02"}]
    else:
        saved["document_hits"] = 2
    with pytest.raises(ValueError, match="RAW_"):
        m.replay_month(saved, Cache())


def test_wrong_source_count_cannot_pass() -> None:
    with pytest.raises(ValueError, match="QUOTAS"):
        m.verify_selection({"entries": []})


def test_replay_preserves_selection_identity_across_later_commits() -> None:
    original: dict[str, Any] = {
        "protocol_preflight": {
            "code_commit": "a",
            "tracked_changes_present": False,
            "freeze_commit": "frozen",
            "manifest_sha256": "f" * 64,
        },
        "entries": ["original"],
    }
    replay = json.loads(json.dumps(original))
    replay["protocol_preflight"].update(code_commit="later", tracked_changes_present=True)
    assert m.stable_manifest(original) == m.stable_manifest(replay)
    assert original["protocol_preflight"]["code_commit"] == "a"
    for field in ("freeze_commit", "manifest_sha256"):
        changed = json.loads(json.dumps(replay))
        changed["protocol_preflight"][field] = "changed"
        assert m.stable_manifest(original) != m.stable_manifest(changed)
    replay["entries"] = ["different"]
    assert m.stable_manifest(original) != m.stable_manifest(replay)


def test_selection_replay_restores_writer_and_never_overwrites_manifest(monkeypatch: Any) -> None:
    manifest = {"protocol_preflight": {"code_commit": "original"}, "entries": ["a"]}
    writer = m.m.immutable

    def fake_freeze() -> None:
        m.m.immutable(
            Path("bounded-source-manifest.json"),
            {"protocol_preflight": {"code_commit": "later"}, "entries": ["changed"]},
        )

    monkeypatch.setattr(m.m, "freeze", fake_freeze)
    with pytest.raises(ValueError, match="SELECTION_REPLAY_MISMATCH"):
        m.replay_selection(manifest)
    assert m.m.immutable is writer
