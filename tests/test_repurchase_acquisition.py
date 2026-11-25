"""Exercise the active bounded client with synthetic SEC containers and no network."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "bounded_acquisition_test", ROOT / "tools/corporate_events/repurchase_acquisition.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)

URL = "https://www.sec.gov/Archives/edgar/data/123/0000000123-06-000001.txt"
ENTRY = {
    "source_url": URL,
    "accession": "0000000123-06-000001",
    "form": "8-K",
    "filed": "2006-01-10",
    "cik_from_historical_index": "123",
}
RAW = b"""<SEC-DOCUMENT>0000000123-06-000001.txt : 20060110
<SEC-HEADER>
ACCESSION NUMBER: 0000000123-06-000001
CONFORMED SUBMISSION TYPE: 8-K
FILED AS OF DATE: 20060110
FILER:
 CENTRAL INDEX KEY: 0000000123
</SEC-HEADER>
<DOCUMENT>
<TYPE>8-K
<SEQUENCE>1
<FILENAME>report.htm
<TEXT>
<html>Unclassified synthetic source.</html>
</TEXT>
</DOCUMENT>
</SEC-DOCUMENT>
"""


def response(status: int = 200, body: bytes = RAW, **headers: str) -> Any:
    return m.sec.Response(
        status, {"content-type": "text/plain", **headers}, body if status == 200 else b""
    )


class Transport:
    def __init__(self, replies: list[Any]) -> None:
        self.replies, self.calls, self.last_bytes = replies, 0, 0
        self.budget: Any = None

    def get(self, url: str, agent: str) -> Any:
        self.calls += 1
        reply = self.replies.pop(0)
        self.last_bytes = len(reply.body)
        self.budget.charge(self.last_bytes)
        return reply


def client(tmp_path: Path, replies: list[Any], scheduler: Any = None) -> tuple[Any, Transport, Any]:
    if scheduler is None:
        now = [100.0]

        def clock() -> float:
            return now[0]

        def sleep(seconds: float) -> None:
            now[0] += seconds

        scheduler = m.sec.Scheduler(tmp_path / "shared", clock=clock, sleep=sleep)
        scheduler.__enter__()
    transport = Transport(replies)
    c = m.Client(
        tmp_path / "bodies",
        [ENTRY],
        scheduler,
        "Research Example example@example.invalid",
        transport,
    )
    transport.budget = c.budget
    return c, transport, scheduler


def test_container_validation_never_certifies_event_or_public_time() -> None:
    proof = m.check_submission(RAW, ENTRY)
    assert proof["document_blocks"] == 1
    assert proof["event_extraction_performed"] is False
    assert proof["public_dissemination_time_certified"] is False
    assert proof["issuer_identity_certified"] is False


@pytest.mark.parametrize(
    "old,new",
    [
        (b"ACCESSION NUMBER: 0000000123-06-000001", b"ACCESSION NUMBER: 0000000123-06-000002"),
        (b"CONFORMED SUBMISSION TYPE: 8-K", b"CONFORMED SUBMISSION TYPE: 10-K"),
        (b"FILED AS OF DATE: 20060110", b"FILED AS OF DATE: 20060111"),
        (b"CENTRAL INDEX KEY: 0000000123", b"CENTRAL INDEX KEY: 0000000456"),
        (b"<TYPE>8-K", b"<TYPE>EX-99.1"),
        (b"</DOCUMENT>", b""),
        (b"</SEC-DOCUMENT>", b""),
        (b"ACCESSION NUMBER:", b"UNKNOWN:"),
    ],
)
def test_identity_and_incomplete_containers_fail(old: bytes, new: bytes) -> None:
    with pytest.raises(ValueError):
        m.check_submission(RAW.replace(old, new), ENTRY)


@pytest.mark.parametrize(
    "url",
    [
        URL.replace("www.sec.gov", "example.com"),
        URL + "?x=1",
        URL + "#x",
        URL.replace("https:", "http:"),
        URL.replace("000001.txt", "000002.txt"),
    ],
)
def test_outside_frozen_source_set_never_networks(tmp_path: Path, url: str) -> None:
    c, transport, _ = client(tmp_path, [response()])
    with pytest.raises(ValueError):
        c.fetch(url)
    assert transport.calls == 0


def test_success_then_resume_uses_hash_verified_cache_without_new_budget(tmp_path: Path) -> None:
    c, transport, scheduler = client(tmp_path, [response()])
    raw, record = c.fetch(URL)
    assert raw == RAW and record["sha256"] == m.sec.sha(RAW)
    resumed, again, _ = client(tmp_path, [], scheduler)
    assert resumed.fetch(URL)[0] == RAW and again.calls == 0
    assert resumed.state["requests"] == transport.calls == 1
    assert resumed.budget.spent == len(RAW)
    c.cache.object_path(record["sha256"]).write_bytes(b"corrupt")
    with pytest.raises(m.sec.AcquisitionError, match="HASH_MISMATCH"):
        resumed.fetch(URL)


def test_403_blocks_immediately_and_does_not_follow_other_paths(tmp_path: Path) -> None:
    c, transport, scheduler = client(tmp_path, [response(403), response()])
    with pytest.raises(ValueError, match="403"):
        c.fetch(URL)
    assert transport.calls == 1 and scheduler.state()["blocked_reason"]
    with pytest.raises(ValueError, match="BLOCKED"):
        c.fetch(URL)
    assert transport.calls == 1


def test_two_429_responses_persist_stop_across_restart(tmp_path: Path) -> None:
    c, transport, scheduler = client(tmp_path, [response(429), response(429), response()])
    with pytest.raises(ValueError, match="TWO_CONSECUTIVE"):
        c.fetch(URL)
    assert transport.calls == 2
    with pytest.raises(ValueError, match="TWO_CONSECUTIVE"):
        client(tmp_path, [response()], scheduler)


def test_500_retry_preserves_pacing_and_charges_both_requests(tmp_path: Path) -> None:
    c, transport, scheduler = client(tmp_path, [response(500), response()])
    assert c.fetch(URL)[0] == RAW
    assert transport.calls == c.state["requests"] == 2
    rows = [json.loads(x) for x in (c.root / "request-plan.jsonl").read_bytes().splitlines()]
    assert rows[1]["request_start_epoch"] - rows[0]["request_start_epoch"] >= 1
    assert scheduler.attempts_used(URL) == 0


def test_redirect_stops_without_following(tmp_path: Path) -> None:
    c, transport, _ = client(tmp_path, [response(302, location=URL), response()])
    with pytest.raises(ValueError, match="HTTP_STOP_302"):
        c.fetch(URL)
    assert transport.calls == 1


@pytest.mark.parametrize("kind", ["requests", "attempts", "bytes"])
def test_exhausted_budgets_stop_before_network(tmp_path: Path, kind: str) -> None:
    c, transport, scheduler = client(tmp_path, [response()])
    if kind == "requests":
        c.state["requests"] = m.REQUEST_CAP
    elif kind == "attempts":
        for _ in range(5):
            scheduler.record_attempt(URL)
    else:
        c.budget.charge(m.BYTE_CAP)
    with pytest.raises(ValueError):
        c.fetch(URL)
    assert transport.calls == 0


def test_deleted_budget_is_not_silently_reset(tmp_path: Path) -> None:
    c, _, scheduler = client(tmp_path, [])
    c.state_path.unlink()
    with pytest.raises(ValueError, match="MISSING_NO_RESET"):
        client(tmp_path, [], scheduler)


@pytest.mark.parametrize(
    "headers", [{"content-encoding": "gzip"}, {"content-type": "application/json"}]
)
def test_wrong_response_profile_is_quarantined(tmp_path: Path, headers: dict[str, str]) -> None:
    reply = response()
    reply.headers.update(headers)
    c, _, _ = client(tmp_path, [reply])
    with pytest.raises(ValueError, match="TYPE_OR_ENCODING"):
        c.fetch(URL)
    assert c.cache.latest(URL) is None
    assert c.cache.latest("quarantine:" + URL) is not None


def test_200_block_page_blocks_and_preserves_raw_response(tmp_path: Path) -> None:
    raw = b"<html><title>Access denied</title></html>"
    c, transport, scheduler = client(tmp_path, [response(body=raw)])
    with pytest.raises(ValueError, match="BLOCKED_PAGE"):
        c.fetch(URL)
    assert scheduler.state()["blocked_reason"] and transport.calls == 1
    assert c.cache.latest("quarantine:" + URL)["sha256"] == m.sec.sha(raw)


def test_local_firewall_denial_is_not_retried(tmp_path: Path) -> None:
    c, transport, _ = client(tmp_path, [])

    def deny(url: str, agent: str) -> Any:
        transport.calls += 1
        raise m.sec.FirewallError("TEST_LOCAL_DENIAL")

    transport.get = deny  # type: ignore[method-assign]
    with pytest.raises(m.sec.FirewallError):
        c.fetch(URL)
    assert transport.calls == c.state["requests"] == 1
