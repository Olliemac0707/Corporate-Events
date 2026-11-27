"""Reject ambiguous ledger recovery while preserving recorded high-water counts."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "forensic_test", ROOT / "tools/corporate_events/sec_continuation_forensic_replay.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def states() -> tuple[dict[str, Any], dict[str, Any]]:
    attempt: dict[str, Any] = {
        "url": m.continuation.URL,
        "start": m.continuation.START,
        "end": m.continuation.END,
        "status": 206,
        "outcome": "RESERVED",
        "bytes": 7,
        "headers": {"etag": "v"},
    }
    old: dict[str, Any] = {
        "binding": "a",
        "status": "RUNNING",
        "stop_reason": None,
        "attempts": [attempt],
        "received_body_bytes": 7,
    }
    new = copy.deepcopy(old)
    new["attempts"][0]["bytes"] = 10
    new["received_body_bytes"] = 10
    return old, new


def test_valid_pending_state_retains_higher_byte_count() -> None:
    old, new = states()
    assert m.validate_states(old, new, 7) == {
        "durable_received_bytes": 7,
        "pending_received_high_water_bytes": 10,
        "bytes_not_preserved_in_fragment": 3,
    }


@pytest.mark.parametrize(
    "kind",
    [
        "saved_length",
        "wrong_status",
        "wrong_url",
        "wrong_range",
        "different_headers",
        "backwards",
        "over_cap",
        "too_big_gap",
        "changed_binding",
    ],
)
def test_ambiguous_or_tampered_state_fails_closed(kind: str) -> None:
    old, new = states()
    length = 7
    if kind == "saved_length":
        length = 6
    elif kind == "wrong_status":
        old["status"] = "STOPPED"
    elif kind == "wrong_url":
        old["attempts"][0]["url"] = "https://example.com"
    elif kind == "wrong_range":
        old["attempts"][0]["end"] -= 1
    elif kind == "different_headers":
        new["attempts"][0]["headers"]["etag"] = "changed"
    elif kind == "backwards":
        new["attempts"][0]["bytes"] = 6
        new["received_body_bytes"] = 6
    elif kind == "over_cap":
        new["attempts"][0]["bytes"] = m.continuation.CAP + 1
        new["received_body_bytes"] = m.continuation.CAP + 1
    elif kind == "too_big_gap":
        new["attempts"][0]["bytes"] = 1048584
        new["received_body_bytes"] = 1048584
    else:
        new["binding"] = "changed"
    with pytest.raises(ValueError):
        m.validate_states(old, new, length)
