"""Offline response framing, discovery semantics, sealed budgets and authorization tests."""

from __future__ import annotations

import copy
import http.client
import importlib.util
import json
import ssl
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "directory_test", ROOT / "tools/corporate_events/sec_feed_directory.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def listing(names: list[str] | None = None) -> bytes:
    return json.dumps({"directory": {
        "name": "/Archives/edgar/Feed/2007/QTR3",
        "item": [{"name": name} for name in (names or ["20070802.nc.tar.gz"])],
    }}).encode()


def fields(size: int) -> list[tuple[str, str]]:
    return [("Content-Length", str(size)), ("Content-Type", "application/json")]


class Reply:
    def __init__(self, body: bytes, status: int = 200, interrupt: bool = False) -> None:
        self.body, self.status, self.interrupt = body, status, interrupt
        self.offset = 0

    def getheaders(self) -> list[tuple[str, str]]:
        return fields(len(self.body))

    def read(self, count: int) -> bytes:
        if self.interrupt:
            self.interrupt = False
            raise http.client.IncompleteRead(self.body[:7], count)
        part = self.body[self.offset:self.offset + count]
        self.offset += len(part)
        return part

    def isclosed(self) -> bool:
        return self.offset == len(self.body)


class Connection:
    def __init__(self, reply: Reply) -> None:
        self.reply, self.closed = reply, False
        self.calls: list[Any] = []

    def request(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append((args, kwargs))

    def getresponse(self) -> Reply:
        return self.reply

    def close(self) -> None:
        self.closed = True


def test_listing_discovers_names_without_timing_claims() -> None:
    result = m.parse_listing(listing([
        "dissem2_08-02-2007.EOD", "20070802.nc.tar.gz", "members.idx", "unknown.bin",
    ]), m.URLS[0])
    assert result["listing_items"] == 4
    assert result["automatic_followup_requests"] == result["public_times_certified"] == 0
    assert result["accession_membership_proven"] is False
    assert result["directory_dates_used_as_public_time"] is False
    assert {x["category"] for x in result["observations"]} == {
        "POSSIBLE_DAILY_AUDIT_REQUIRES_SEPARATE_VALIDATION", "ARCHIVE_NO_AUTOMATIC_DOWNLOAD",
        "POSSIBLE_METADATA_REQUIRES_SEPARATE_VALIDATION", "OTHER_LISTED_NAME",
    }


@pytest.mark.parametrize("name", ["../evil", "a/b", "a\\b", "https:evil", "a%2fb", "a\n", "a..b"])
def test_unsafe_names_are_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="UNSAFE"):
        m.parse_listing(listing([name]), m.URLS[0])


@pytest.mark.parametrize("body", [
    b'{}', b'{"directory":[]}', b'{"directory":{"name":"other","item":[]}}',
    b'{"directory":{},"directory":{}}', b'[]', b'not json',
])
def test_untrusted_schema_rejected(body: bytes) -> None:
    with pytest.raises(ValueError):
        m.parse_listing(body, m.URLS[0])


def test_duplicate_name_and_large_body_rejected() -> None:
    with pytest.raises(ValueError):
        m.parse_listing(listing(["a", "a"]), m.URLS[0])
    with pytest.raises(ValueError):
        m.parse_listing(b" " * (m.CAP + 1), m.URLS[0])
    with pytest.raises(ValueError):
        m.parse_listing(listing(), "https://example.com/index.json")


@pytest.mark.parametrize("status", [206, 301, 302, 403, 404, 429, 500])
def test_no_redirect_retry_or_fallback(status: int) -> None:
    with pytest.raises(ValueError, match=f"HTTP_{status}_STOP"):
        m.checked_headers(status, fields(10))


@pytest.mark.parametrize("extra", [
    [("Content-Length", "9")], [("Transfer-Encoding", "chunked")],
    [("Content-Encoding", "gzip")], [("Content-Range", "bytes 0-9/20")],
    [("Content-Type", "text/html")],
])
def test_bad_framing_rejected(extra: list[tuple[str, str]]) -> None:
    with pytest.raises(ValueError):
        m.checked_headers(200, fields(10) + extra)


@pytest.mark.parametrize("length", ["0", "-1", "unknown", "524289", "9999999999999"])
def test_invalid_lengths_rejected(length: str) -> None:
    with pytest.raises(ValueError):
        m.checked_headers(200, [("content-length", length), ("content-type", "application/json")])


def test_ledger_seals_attempts_and_cap_across_restart(tmp_path: Path) -> None:
    ledger = m.Ledger(tmp_path / "cache", "binding", m.URLS)
    ledger.reserve(m.URLS[0])
    ledger.charge(m.CAP)
    with pytest.raises(ValueError, match="BYTE_CAP"):
        ledger.charge(1)
    with pytest.raises(ValueError, match="BYTE_CAP"):
        m.Ledger(tmp_path / "cache", "binding", m.URLS)
    assert json.loads((tmp_path / "cache/state.json").read_bytes())["status"] == "STOPPED"


def test_retry_forbidden_after_restart(tmp_path: Path) -> None:
    ledger = m.Ledger(tmp_path / "cache", "binding", m.URLS)
    ledger.reserve(m.URLS[0])
    other = m.Ledger(tmp_path / "cache", "binding", m.URLS)
    with pytest.raises(ValueError, match="ATTEMPT_FORBIDDEN"):
        other.reserve(m.URLS[0])


@pytest.mark.parametrize("interrupt", [False, True])
def test_transport_charges_bytes_and_never_follows_listing(tmp_path: Path, interrupt: bool) -> None:
    ledger = m.Ledger(tmp_path / "cache", "binding", m.URLS)
    ledger.reserve(m.URLS[0])
    connection = Connection(Reply(listing(), interrupt=interrupt))
    transport = m.Transport(ssl.create_default_context(), lambda *a, **k: connection)
    if interrupt:
        with pytest.raises(ValueError, match="PARTIAL"):
            transport.get(m.URLS[0], "private-test-agent", ledger)
        assert ledger.state["received_body_bytes"] == 7
    else:
        assert transport.get(m.URLS[0], "private-test-agent", ledger) == listing()
        assert ledger.state["received_body_bytes"] == len(listing())
    ledger.finish(transport.body, "TEST_ONLY", [])
    digest = ledger.state["attempts"][0]["body_sha256"]
    assert m.sec.Cache(ledger.root).read(digest) == transport.body
    assert len(connection.calls) == 1
    assert "Range" not in connection.calls[0][1]["headers"]
    assert connection.closed
    assert m.sec.NETWORK_HOST.get() is None


def test_transport_requires_reservation_and_verified_tls(tmp_path: Path) -> None:
    ledger = m.Ledger(tmp_path / "cache", "binding", m.URLS)
    context = ssl.create_default_context()
    transport = m.Transport(context, lambda *a, **k: pytest.fail("must not connect"))
    with pytest.raises(ValueError, match="UNRESERVED"):
        transport.get(m.URLS[0], "private-test-agent", ledger)
    ledger.reserve(m.URLS[0])
    context.check_hostname = False
    with pytest.raises(ValueError, match="TLS"):
        transport.get(m.URLS[0], "private-test-agent", ledger)


def test_authorization_must_bind_new_registry(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(m, "PRIVATE", tmp_path)
    with pytest.raises(ValueError, match="FRESH_EXPLICIT"):
        m.authorized("binding")
    approval = {"approved": True, "registry_binding": "old", "scope":
                "FOUR_FEED_DIRECTORY_JSON_REQUESTS_ONLY", "verbatim_user_approval": "approved"}
    path = tmp_path / "feed-directory-authorization-v1.json"
    path.write_text(json.dumps(approval))
    with pytest.raises(ValueError, match="FRESH_EXPLICIT"):
        m.authorized("binding")
    approval["registry_binding"] = "binding"
    path.write_text(json.dumps(approval))
    assert m.authorized("binding") == approval


def test_old_global_attempts_preserved(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(m, "PRIVATE", tmp_path)
    prior = {"old-archive-url": 1}
    (tmp_path / "snapshot.json").write_text(json.dumps(prior))
    policy = {"attempt_snapshot": "snapshot.json"}
    for attempts in [prior, {**prior, m.URLS[0]: 1}]:
        (tmp_path / "scheduler-state.json").write_text(json.dumps({"attempts_by_url": attempts}))
        m.verify_prior_attempts(policy)
    for attempts in [{"old-archive-url": 0}, {**prior, m.URLS[0]: 2}, {**prior, "new-host": 1}]:
        (tmp_path / "scheduler-state.json").write_text(json.dumps({"attempts_by_url": attempts}))
        with pytest.raises(ValueError, match="PRIOR_ATTEMPTS"):
            m.verify_prior_attempts(copy.deepcopy(policy))


@pytest.mark.parametrize("status", [403, 404, 429, 301])
def test_denials_do_not_read_body(tmp_path: Path, status: int) -> None:
    ledger = m.Ledger(tmp_path / "cache", "binding", m.URLS)
    ledger.reserve(m.URLS[0])
    reply = Reply(listing(), status=status)
    connection = Connection(reply)
    transport = m.Transport(ssl.create_default_context(), lambda *a, **k: connection)
    with pytest.raises(ValueError, match=f"HTTP_{status}"):
        transport.get(m.URLS[0], "private-test-agent", ledger)
    assert ledger.state["received_body_bytes"] == reply.offset == 0
    assert connection.closed
    with pytest.raises(ValueError, match="UNRESERVED"):
        transport.get(m.URLS[0], "private-test-agent", ledger)


def test_constructor_failure_restores_network_host(tmp_path: Path) -> None:
    ledger = m.Ledger(tmp_path / "cache", "binding", m.URLS)
    ledger.reserve(m.URLS[0])

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise TimeoutError("synthetic constructor timeout")

    transport = m.Transport(ssl.create_default_context(), fail)
    with pytest.raises(TimeoutError):
        transport.get(m.URLS[0], "private-test-agent", ledger)
    assert m.sec.NETWORK_HOST.get() is None


def test_acquire_without_new_approval_never_reads_contact_or_connects(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    monkeypatch.setattr(m, "PRIVATE", tmp_path)
    monkeypatch.setattr(m, "preflight", lambda: ({}, "binding"))
    monkeypatch.setattr(m.sec, "private_contact", lambda *a: pytest.fail("approval first"))
    with pytest.raises(ValueError, match="FRESH_EXPLICIT"):
        m.run("acquire")
    assert not list(tmp_path.iterdir())


def test_four_unique_attempts_and_total_cap_survive_restart(tmp_path: Path) -> None:
    ledger = m.Ledger(tmp_path / "cache", "binding", m.URLS)
    for url in m.URLS:
        ledger.reserve(url)
        ledger.charge(m.CAP)
        ledger = m.Ledger(tmp_path / "cache", "binding", m.URLS)
    assert ledger.state["received_body_bytes"] == m.TOTAL
    with pytest.raises(ValueError, match="ATTEMPT_FORBIDDEN"):
        ledger.reserve(m.URLS[0])
