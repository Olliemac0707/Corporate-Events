"""Security and provenance checks for the bounded SEC evidence capability."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "evidence_tool_tests", ROOT / "tools/corporate_events/authority_evidence.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
d = m.documents
URL = "https://www.sec.gov/files/test.pdf"


def plan() -> dict[str, Any]:
    return {
        "version": "authority-evidence-plan-v1",
        "entries": [{"url": URL, "kind": "OFFICIAL_AUTHORITY", "purpose": "historical authority"}],
        "redirects": [],
        "max_received_body_bytes": 134217728,
        **dict.fromkeys(
            (
                "market_data_allowed",
                "full_extraction_authorized",
                "final_qa_authorized",
                "phase3_authorized",
                "sample_changes_allowed",
                "parser_changes_allowed",
            ),
            False,
        ),
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://www.sec.gov/files/test.pdf",
        "https://data.sec.gov/files/test.pdf",
        "https://query1.finance.yahoo.com/files/test.pdf",
        "https://www.sec.gov.evil.test/files/test.pdf",
        "https://name@www.sec.gov/files/test.pdf",
        "https://www.sec.gov:444/files/test.pdf",
        URL + "?secret=1",
        URL + "#fragment",
        "https://www.sec.gov/files/../test.pdf",
        "https://www.sec.gov/files/%2e%2e/test.pdf",
    ],
)
def test_bad_urls_rejected_even_if_listed(url: str) -> None:
    with pytest.raises(ValueError):
        d.validate_url(url, {url})


def test_unplanned_url_is_rejected() -> None:
    with pytest.raises(ValueError):
        d.validate_url(URL, set())


@pytest.mark.parametrize(
    "field",
    [
        "market_data_allowed",
        "full_extraction_authorized",
        "final_qa_authorized",
        "phase3_authorized",
        "sample_changes_allowed",
        "parser_changes_allowed",
    ],
)
def test_closed_gates_remain_closed(field: str) -> None:
    value = plan()
    value[field] = True
    with pytest.raises(ValueError):
        m.check_plan(value)


def test_valid_authority_plan_and_submission_cap() -> None:
    m.check_plan(plan())
    value = plan()
    value["entries"] = [
        {
            "url": f"https://www.sec.gov/Archives/edgar/data/123/0000000123-24-{i:06}.txt",
            "kind": "COMPLETE_SUBMISSION",
            "purpose": "dated evidence",
        }
        for i in range(13)
    ]
    with pytest.raises(ValueError, match="FILING_CAP"):
        m.check_plan(value)


def test_filing_cannot_be_disguised_as_documentation() -> None:
    value = plan()
    value["entries"][0]["url"] = (
        "https://www.sec.gov/Archives/edgar/data/123/0000000123-24-000001.txt"
    )
    with pytest.raises(ValueError, match="HIDE"):
        m.check_plan(value)


@pytest.mark.parametrize(
    "raw,url",
    [
        (b"<html>error</html>", URL),
        (b"not a zip", "https://www.sec.gov/files/test.zip"),
        (
            b"<html>filing</html>",
            "https://www.sec.gov/Archives/edgar/data/123/0000000123-24-000001.txt",
        ),
    ],
)
def test_wrong_payload_kind_rejected(raw: bytes, url: str) -> None:
    with pytest.raises(ValueError):
        d.validate_payload(raw, url)


def test_blocked_page_is_never_cached_as_an_authority() -> None:
    with pytest.raises(ValueError, match="BLOCKED"):
        d.validate_payload(
            b"<html>Your request originates from an undeclared automated tool</html>", URL
        )


def test_received_byte_budget_persists_and_cannot_reset(tmp_path: Path) -> None:
    path = tmp_path / "budget.json"
    budget = d.ByteBudget(path, 100, "fixed")
    budget.charge(60)
    same = d.ByteBudget(path, 100, "fixed")
    assert same.spent == 60 and same.cap() == 39
    with pytest.raises(ValueError):
        d.ByteBudget(path, 101, "fixed")
    with pytest.raises(ValueError):
        same.charge(41)


def test_http_403_blocks_shared_scheduler_without_retry_or_alias(tmp_path: Path) -> None:
    class Scheduler:
        def __init__(self) -> None:
            self.used = 0
            self.blocked: str | None = None
            self.last_delay = 0.0

        def state(self) -> dict[str, Any]:
            return {}

        def attempts_used(self, url: str) -> int:
            return self.used

        def start(self, deadline: float) -> float:
            return 0.0

        def record_attempt(self, url: str) -> None:
            self.used += 1

        def block(self, reason: str) -> None:
            self.blocked = reason

    class Transport:
        def get(self, url: str, agent: str) -> Any:
            return m.sec.Response(403, {}, b"")

    scheduler = Scheduler()
    client = d.DocumentClient(
        m.sec, tmp_path, tmp_path, scheduler, Transport(), {URL}, "private", 100.0
    )
    with pytest.raises(m.sec.AcquisitionError, match="403"):
        client.fetch(URL)
    assert scheduler.used == 1 and scheduler.blocked == "HTTP_403_STOP_NO_EVASION"
    assert not (tmp_path / "object-versions.jsonl").exists()


def test_cumulative_filing_cap_does_not_reset_with_new_plan() -> None:
    records = [
        {"url": f"https://www.sec.gov/Archives/edgar/data/1/0000000001-24-{i:06}.txt"}
        for i in range(12)
    ]
    m.check_object_budget(records[0]["url"], records)
    with pytest.raises(ValueError, match="GLOBAL_FILING_BUDGET"):
        m.check_object_budget(
            "https://www.sec.gov/Archives/edgar/data/1/0000000001-24-000099.txt", records
        )


def test_cumulative_object_cap_does_not_reset_with_new_plan() -> None:
    records = [{"url": f"https://www.sec.gov/files/spec{i}.pdf"} for i in range(48)]
    with pytest.raises(ValueError, match="GLOBAL_OBJECT_BUDGET"):
        m.check_object_budget(URL, records)
