"""Network and persistence boundaries for the metadata-only search client."""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


m = load("search_client_test", "tools/corporate_events/sec_search_metadata_client.py")
sec = load("search_sec_test", "tools/corporate_events/sec_census.py")


class Scheduler:
    def __init__(self) -> None:
        self.used: dict[str, int] = {}
        self.blocked: str | None = None
        self.holds: list[float] = []

    def attempts_used(self, key: str) -> int:
        return self.used.get(key, 0)

    def start(self, deadline: float) -> float:
        assert deadline > time.time()
        if self.blocked:
            raise ValueError("BLOCKED")
        return time.time()

    def record_attempt(self, key: str) -> None:
        self.used[key] = self.attempts_used(key) + 1

    def record_success(self, key: str) -> None:
        self.used[key] = 0

    def hold(self, delay: float, reason: str) -> None:
        assert reason
        self.holds.append(delay)

    def block(self, reason: str) -> None:
        self.blocked = reason


def client(tmp_path: Path, replies: list[Any], cap: int = 12) -> tuple[Any, Scheduler, list[str]]:
    scheduler = Scheduler()
    calls: list[str] = []

    def transport(url: str, method: str, payload: object, agent: str) -> Any:
        assert method and agent
        calls.append(url)
        return replies.pop(0)

    return (
        m.Client(
            sec, tmp_path, scheduler, "PRIVATE CONTACT", "diagnostic", cap, 33554432, transport
        ),
        scheduler,
        calls,
    )


def response(
    status: int = 200,
    body: bytes = b'{"ok":true}',
    headers: dict[str, str] | None = None,
    **extra: str,
) -> Any:
    return sec.Response(
        status, {"content-type": "application/json", **(headers or {}), **extra}, body
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://www.sec.gov/edgar/search/",
        "https://evil.test/edgar/search/",
        "https://www.sec.gov.evil.test/edgar/search/",
        "https://u@www.sec.gov/edgar/search/",
        "https://www.sec.gov:444/edgar/search/",
        m.PAGE + "?x=1",
        m.PAGE + "#x",
        "https://www.sec.gov/Archives/edgar/data/1/filing.txt",
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json",
        "https://www.sec.gov/edgar/search/../filing.js",
        "https://www.sec.gov/edgar/search/%2e%2e/filing.js",
    ],
)
def test_forbidden_targets(url: str) -> None:
    with pytest.raises(ValueError):
        m.validate_request(url, "GET", None)


def test_allowed_authority_and_endpoint_shapes() -> None:
    m.validate_request(m.PAGE, "GET", None)
    m.validate_request("https://www.sec.gov/edgar/search/js/efts.js", "GET", None)
    m.validate_request(m.SEARCH, "POST", {})
    with pytest.raises(ValueError):
        m.validate_request(m.PAGE, "POST", {})
    with pytest.raises(ValueError):
        m.validate_request(m.SEARCH, "GET", None)


def test_403_stops_and_blocks_without_alias(tmp_path: Path) -> None:
    c, s, calls = client(tmp_path, [response(403)])
    with pytest.raises(ValueError, match="403"):
        c.fetch(m.SEARCH, "POST", {})
    assert s.blocked and calls == [m.SEARCH]


def test_two_429_persist_stop_and_retry_after(tmp_path: Path) -> None:
    c, s, calls = client(tmp_path, [response(429, headers={"retry-after": "120"}), response(429)])
    with pytest.raises(ValueError, match="TWO_CONSECUTIVE"):
        c.fetch(m.SEARCH, "POST", {})
    assert len(calls) == 2 and s.holds[0] >= 120
    with pytest.raises(ValueError, match="TWO_CONSECUTIVE"):
        m.Client(sec, tmp_path, s, "private", "diagnostic", 12, 33554432)


def test_redirect_not_followed(tmp_path: Path) -> None:
    c, _, calls = client(tmp_path, [response(302, location="https://evil.test")])
    with pytest.raises(ValueError, match="STOP_302"):
        c.fetch(m.SEARCH, "POST", {})
    assert len(calls) == 1


def test_budget_survives_restart_and_cannot_reset(tmp_path: Path) -> None:
    c, s, _ = client(tmp_path, [response()], cap=1)
    c.fetch(m.SEARCH, "POST", {"q": "one"})
    with pytest.raises(ValueError, match="IDENTITY"):
        m.Client(sec, tmp_path, s, "private", "diagnostic", 2, 33554432)
    restarted = m.Client(sec, tmp_path, s, "private", "diagnostic", 1, 33554432)
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        restarted.fetch(m.SEARCH, "POST", {"q": "two"})


def test_payload_identity_cache_and_hash_validation(tmp_path: Path) -> None:
    c, _, calls = client(tmp_path, [response(), response(body=b'{"ok":false}')])
    one = c.fetch(m.SEARCH, "POST", {"q": "one"})
    assert c.fetch(m.SEARCH, "POST", {"q": "one"}) == one
    assert c.fetch(m.SEARCH, "POST", {"q": "two"}) != one and len(calls) == 2
    record = sec.Cache(tmp_path).latest(
        "search-metadata:"
        + sec.sha(sec.json_bytes({"method": "POST", "url": m.SEARCH, "payload": {"q": "one"}}))
    )
    assert record
    sec.Cache(tmp_path).object_path(record["sha256"]).write_bytes(b"tampered")
    with pytest.raises(sec.AcquisitionError, match="HASH_MISMATCH"):
        c.fetch(m.SEARCH, "POST", {"q": "one"})
    assert "PRIVATE CONTACT" not in (tmp_path / "requests.jsonl").read_text()


def test_shared_url_failure_budget_precedes_new_payload(tmp_path: Path) -> None:
    c, s, calls = client(tmp_path, [response()])
    s.used[m.SEARCH] = 5
    with pytest.raises(ValueError, match="ATTEMPT_CAP"):
        c.fetch(m.SEARCH, "POST", {"q": "different"})
    assert not calls


@pytest.mark.parametrize(
    "reply",
    [
        response(body=b"not json"),
        response(body=b"[]"),
        response(headers={"content-type": "text/html"}),
        response(headers={"content-encoding": "gzip"}),
    ],
)
def test_invalid_response_never_enters_cache(tmp_path: Path, reply: Any) -> None:
    c, _, _ = client(tmp_path, [reply])
    with pytest.raises(ValueError):
        c.fetch(m.SEARCH, "POST", {})
    assert not (tmp_path / "object-versions.jsonl").exists()
