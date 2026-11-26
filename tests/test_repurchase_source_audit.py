"""Test control integrity and clock evidence without certifying scientific QA."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "source_audit_test", ROOT / "tools/corporate_events/repurchase_source_audit.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def header(acceptance: bytes = b"20241103013000") -> bytes:
    return (
        b"<SEC-HEADER>\n<ACCEPTANCE-DATETIME>"
        + acceptance
        + b"\nPUBLIC DOCUMENT COUNT: 2\nFILED AS OF DATE: 20241103\n"
        b"CONFORMED SUBMISSION TYPE: 8-K\nCENTRAL INDEX KEY: 0000000042\n"
        b"STATE OF INCORPORATION: DE\n<DOCUMENT>\n"
        b"<ACCEPTANCE-DATETIME>19990101000000\nbody"
    )


def test_acceptance_is_retained_raw_without_assuming_timezone_or_public_time() -> None:
    result = m.header_observations(header())
    assert result["acceptance_raw_values"] == ["20241103013000"]
    assert result["acceptance_calendar_valid"]
    assert result["acceptance_datetime_et"] is None
    assert result["acceptance_datetime_utc"] is None
    assert result["registered_public_datetime"] is None
    assert result["verified_dissemination_datetime"] is None
    assert not result["source_timezone_semantics_verified"]


@pytest.mark.parametrize("value", [b"20240231010000", b"not-a-timestamp", b"20240101"])
def test_invalid_acceptance_is_not_silently_normalized(value: bytes) -> None:
    result = m.header_observations(header(value))
    assert not result["acceptance_calendar_valid"]
    assert result["acceptance_raw_values"] == [value.decode()]


def test_duplicate_header_times_remain_ambiguous() -> None:
    raw = header(b"20240101010000\n<ACCEPTANCE-DATETIME>20240101020000")
    result = m.header_observations(raw)
    assert len(result["acceptance_raw_values"]) == 2
    assert not result["acceptance_calendar_valid"]


def test_raw_filer_descriptions_do_not_certify_security_eligibility() -> None:
    result = m.header_observations(header())
    assert result["header_ciks"] == ["42"]
    assert result["incorporation_codes_as_stated"] == ["DE"]
    assert not result["historical_security_eligibility_certified"]


def test_missing_header_boundary_stops_the_audit() -> None:
    with pytest.raises(ValueError, match="BOUNDARY"):
        m.header_observations(b"<ACCEPTANCE-DATETIME>20240101010000")


def test_control_crosscheck_can_find_wording_without_repurchase_keyword() -> None:
    result = m.control_cues("The Board authorized us to acquire outstanding common shares.")
    assert result["total_cue_hits"] > 0
    assert {r["cue"] for r in result["cues"]} == {
        "COMMON_SHARE_ACQUISITION_WORDING",
        "BOARD_AUTHORITY_AND_COMMON_SHARES",
    }
    assert not result["event_presence_assessed"]


def test_negated_purchases_are_context_not_a_certified_negative_control() -> None:
    text = "No shares were repurchased during the quarter."
    result = m.control_cues(text)
    assert result["total_cue_hits"] == 1
    assert not result["event_absence_certified"]
    for row in result["cues"]:
        assert text[row["text_start"] : row["text_end"]] == row["quote"]


def test_zero_crosscheck_cues_does_not_mean_no_event() -> None:
    result = m.control_cues("The company described its operating results.")
    assert result["total_cue_hits"] == 0
    assert not result["event_absence_certified"]


def test_excerpt_cap_reports_every_omitted_hit_count() -> None:
    result = m.control_cues("repurchased " * 8, cap=2)
    assert result["total_cue_hits"] == 8
    assert result["retained_cue_hits"] == 2
    assert result["cue_excerpt_cap_reached"]


def test_archive_matches_require_exact_original_member_names() -> None:
    acc = "0000000042-10-000001"
    headers = [
        {"name": name}
        for name in [
            acc + ".nc.bak",
            "other/" + acc + ".nc",
            "./" + acc + ".corr01",
            "./" + acc + ".nc",
        ]
    ]
    assert m.archive_matches(headers, {acc}) == [acc]
    assert m.archive_matches(headers[:-1], {acc}) == []


def sources() -> list[dict[str, Any]]:
    return [{"accession": f"control-{i}", "role": "SOURCE_CONTROL"} for i in range(20)] + [
        {"accession": "candidate", "role": "CANDIDATE_HIT"},
        {"accession": "not-selected", "role": "CANDIDATE_HIT"},
    ]


def test_review_bundle_keeps_all_controls_and_deduplicates_related_action_sources() -> None:
    cases = [{"accession": "candidate"}, {"accession": "candidate"}]
    selected = m.reviewer_sources(sources(), cases, "frozen-seed")
    assert len(selected) == 21
    assert sum(r["role"] == "SOURCE_CONTROL" for r in selected) == 20
    assert {r["accession"] for r in selected} == {
        r["accession"] for r in sources() if r["accession"] != "not-selected"
    }
    assert selected == m.reviewer_sources(list(reversed(sources())), cases, "frozen-seed")


@pytest.mark.parametrize("mode", ["missing_control", "unknown_source", "too_many_actions"])
def test_review_packet_cannot_silently_replace_or_expand_its_frame(mode: str) -> None:
    frame = sources()
    cases = [{"accession": "candidate"}]
    if mode == "missing_control":
        frame = frame[1:]
    elif mode == "unknown_source":
        cases = [{"accession": "not-in-frame"}]
    else:
        cases = [{"accession": "candidate"}] * 31
    with pytest.raises(ValueError):
        m.reviewer_sources(frame, cases, "frozen-seed")
