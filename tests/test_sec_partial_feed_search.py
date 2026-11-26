"""Verify partial-search budgets, exact targets, stop behavior and transport controls."""

from __future__ import annotations

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
    "partial_search_test", ROOT / "tools/corporate_events/sec_partial_feed_search.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def target(index: int = 0) -> dict[str, Any]:
    return {
        "url": m.URLS[index],
        "total_bytes": m.SIZES[index],
        "etag": '"a1b2-20"',
        "last_modified": "Thu, 23 Nov 2017 01:14:32 GMT",
        "prefix_sha256": "a" * 64,
        "filings": [{"accession": acc, "source_sha256": "b" * 64} for acc in m.ACCESSIONS[index]],
    }


def headers(t: dict[str, Any], start: int, end: int) -> list[tuple[str, str]]:
    return [
        ("Content-Range", f"bytes {start}-{end}/{t['total_bytes']}"),
        ("Content-Length", str(end - start + 1)),
        ("ETag", t["etag"]),
        ("Last-Modified", t["last_modified"]),
        ("Content-Type", "application/x-tar"),
    ]


def test_scope_and_budget_are_exact() -> None:
    m.validate_targets([target(0), target(1)])
    assert m.CAP == 8 * m.ranges.RANGE_CAP
    for t in [target(0), target(1)]:
        spans = m.ranges.ranges(t["total_bytes"])[:4]
        assert len(spans) == 4 and spans[0][0] == m.ranges.PREFIX
        assert all(end - start + 1 == m.ranges.RANGE_CAP for start, end in spans)


@pytest.mark.parametrize(
    "key,value",
    [
        ("url", "https://example.com"),
        ("total_bytes", 1),
        ("etag", 'W/"weak"'),
        ("prefix_sha256", "short"),
        ("filings", []),
    ],
)
def test_changed_target_scope_rejected(key: str, value: Any) -> None:
    targets = [target(0), target(1)]
    targets[0][key] = value
    with pytest.raises(ValueError):
        m.validate_targets(targets)


def test_ordered_range_budget_and_restart_stop(tmp_path: Path) -> None:
    ledger = m.Ledger(tmp_path / "live", "binding")
    spans = m.ranges.ranges(m.SIZES[0])
    with pytest.raises(ValueError, match="ATTEMPT_FORBIDDEN"):
        ledger.reserve(m.URLS[0], *spans[1])
    ledger.reserve(m.URLS[0], *spans[0])
    ledger.charge(7)
    with pytest.raises(ValueError, match="ATTEMPT_FORBIDDEN"):
        ledger.reserve(m.URLS[0], *spans[1])
    ledger.stop("SYNTHETIC_FAILURE")
    again = m.Ledger(tmp_path / "live", "binding")
    assert again.state["status"] == "STOPPED" and again.state["received_body_bytes"] == 7
    with pytest.raises(ValueError, match="ATTEMPT_FORBIDDEN"):
        again.reserve(m.URLS[0], *spans[1])
    with pytest.raises(ValueError, match="BINDING"):
        m.Ledger(tmp_path / "live", "changed")


def test_per_response_byte_limit_is_durable(tmp_path: Path) -> None:
    ledger = m.Ledger(tmp_path / "live", "binding")
    ledger.reserve(m.URLS[0], *m.ranges.ranges(m.SIZES[0])[0])
    with pytest.raises(ValueError, match="BYTE_CAP"):
        ledger.charge(m.ranges.RANGE_CAP + 1)
    assert m.Ledger(tmp_path / "live", "binding").state["status"] == "STOPPED"


class Reply:
    def __init__(self, t: dict[str, Any], start: int, end: int, status: int = 206) -> None:
        self.status = status
        self.fields = headers(t, start, end)
        self.read_called = False

    def getheaders(self) -> list[tuple[str, str]]:
        return self.fields

    def read(self, n: int) -> bytes:
        self.read_called = True
        raise http.client.IncompleteRead(b"partial", n)

    def isclosed(self) -> bool:
        return True


class Connection:
    def __init__(self, reply: Reply) -> None:
        self.reply = reply
        self.closed = False
        self.calls: list[Any] = []

    def request(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append((args, kwargs))

    def getresponse(self) -> Reply:
        return self.reply

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("status", [206, 200, 301, 403, 429, 500])
def test_no_fallback_and_partial_response_charged(tmp_path: Path, status: int) -> None:
    t = target()
    start, end = m.ranges.ranges(t["total_bytes"])[0]
    ledger = m.Ledger(tmp_path / "live", "binding")
    ledger.reserve(t["url"], start, end)
    reply = Reply(t, start, end, status)
    conn = Connection(reply)
    with pytest.raises(ValueError):
        m.receive(
            t,
            start,
            end,
            "synthetic contact",
            ledger,
            ssl.create_default_context(),
            lambda *a, **k: conn,
        )
    assert ledger.state["received_body_bytes"] == (7 if status == 206 else 0)
    assert reply.read_called is (status == 206)
    assert conn.calls[0][1]["headers"]["If-Range"] == t["etag"]
    assert conn.closed and m.sec.NETWORK_HOST.get() is None


def test_successful_range_is_cached_and_cannot_repeat(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(m.ranges, "RANGE_CAP", 4)
    monkeypatch.setattr(m, "SIZES", [m.ranges.PREFIX + 20, m.SIZES[1]])
    t = target()
    t["total_bytes"] = m.SIZES[0]
    start, end = m.ranges.ranges(t["total_bytes"])[0]
    ledger = m.Ledger(tmp_path / "live", "binding")
    ledger.reserve(t["url"], start, end)

    class CompleteReply(Reply):
        def read(self, n: int) -> bytes:
            return b"abcd"

    conn = Connection(CompleteReply(t, start, end))
    ctx = ssl.create_default_context()
    m.receive(t, start, end, "synthetic contact", ledger, ctx, lambda *a, **k: conn)
    attempt = ledger.state["attempts"][0]
    assert attempt["outcome"] == "RANGE_CACHED"
    assert (ledger.root / attempt["locator"]).read_bytes() == b"abcd"
    with pytest.raises(ValueError, match="UNRESERVED"):
        m.receive(t, start, end, "synthetic contact", ledger, ctx, lambda *a, **k: conn)


def test_prior_counts_cannot_be_reset_or_extended(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(m, "PRIVATE", tmp_path)
    prior = {"old": 5, m.URLS[0]: 1, m.URLS[1]: 1}
    (tmp_path / "partial-feed-search-prior-attempts-v1.json").write_text(json.dumps(prior))
    for counts in [prior, {**prior, m.URLS[0]: 5}]:
        (tmp_path / "scheduler-state.json").write_text(json.dumps({"attempts_by_url": counts}))
        m.verify_prior({})
    for counts in [{**prior, "old": 4}, {**prior, m.URLS[0]: 6}, {**prior, "unapproved": 1}]:
        (tmp_path / "scheduler-state.json").write_text(json.dumps({"attempts_by_url": counts}))
        with pytest.raises(ValueError, match="PRIOR_ATTEMPTS"):
            m.verify_prior({})


@pytest.mark.parametrize("denied", [False, True])
def test_run_stops_on_denial_and_stops_each_archive_after_matches(
    tmp_path: Path, monkeypatch: Any, denied: bool
) -> None:
    monkeypatch.setattr(m, "PRIVATE", tmp_path)
    monkeypatch.setattr(m, "OUTPUT", tmp_path / "output")
    monkeypatch.setattr(
        m,
        "preflight",
        lambda: (
            {"targets": [target(0), target(1)], "policy": {"dependencies": {}}, "registry": {}},
            "binding",
        ),
    )
    monkeypatch.setattr(m, "verify_prior", lambda *a: None)
    monkeypatch.setattr(m.sec, "private_contact", lambda *a: "synthetic contact")
    monkeypatch.setattr(m.sec, "install_firewall", lambda: None)
    monkeypatch.setattr(m.sec, "EXTENSION_FILES", m.sec.EXTENSION_FILES)
    (tmp_path / "partial-feed-search-authorization-v1.json").write_text(
        json.dumps(
            {
                "approved": True,
                "registry_binding": "binding",
                "scope": "TWO_PARTIAL_ARCHIVES_THREE_FILINGS_ONLY",
            }
        )
    )

    class Scheduler:
        def __init__(self, root: Path) -> None:
            self.counts = dict.fromkeys(m.URLS, 1)
            self.blocked = False

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *a: Any) -> None:
            pass

        def attempts_used(self, url: str) -> int:
            return int(self.counts[url])

        def start(self, deadline: float) -> None:
            pass

        def record_attempt(self, url: str) -> None:
            self.counts[url] += 1

        def block(self, reason: str) -> None:
            self.blocked = True

        def hold(self, seconds: float, reason: str) -> None:
            pass

    monkeypatch.setattr(m.sec, "Scheduler", Scheduler)

    def fake_receive(
        t: dict[str, Any], start: int, end: int, agent: str, ledger: Any, context: Any
    ) -> None:
        attempt = ledger.state["attempts"][-1]
        if denied:
            attempt["status"] = 403
            raise ValueError("TWO_ARCHIVE_HTTP_403_STOP")
        raw = b"abc"
        ledger.charge(len(raw))
        path = ledger.root / f"synthetic-{len(ledger.state['attempts'])}"
        path.write_bytes(raw)
        attempt.update(status=206, outcome="RANGE_CACHED", locator=path.name, sha256=m.sec.sha(raw))
        ledger.save()

    def fake_scan(t: dict[str, Any], ledger: Any) -> dict[str, Any]:
        count = sum(a["url"] == t["url"] for a in ledger.state["attempts"])
        ids = [f["accession"] for f in t["filings"]]
        found = ids[: min(count, len(ids))]
        return {
            "matches": [{"accession": a} for a in found],
            "unlocated_accessions": [a for a in ids if a not in found],
        }

    monkeypatch.setattr(m, "receive", fake_receive)
    monkeypatch.setattr(m, "scan_target", fake_scan)
    result = m.run("acquire")
    assert result["HTTP_attempts"] == (1 if denied else 3)
    assert result["target_members_found"] == (0 if denied else 3)
    assert result["status"] == (
        "STOPPED" if denied else "COMPLETED_PARTIAL_MEMBERSHIP_NOT_CLOCK_CERTIFICATION"
    )
    assert result["public_times_certified"] == 0
