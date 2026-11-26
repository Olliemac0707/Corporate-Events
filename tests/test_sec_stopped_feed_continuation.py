"""Check the one-range continuation preserves the stopped evidence and attempt cap."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "continuation_test", ROOT / "tools/corporate_events/sec_stopped_feed_continuation.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def test_single_attempt_and_byte_limit_are_durable(tmp_path: Path) -> None:
    ledger = m.Ledger(tmp_path / "live", "binding")
    ledger.reserve()
    ledger.charge(7)
    with pytest.raises(ValueError, match="ATTEMPT_FORBIDDEN"):
        ledger.reserve()
    with pytest.raises(ValueError, match="BYTE_CAP"):
        ledger.charge(m.CAP)
    replay = m.Ledger(tmp_path / "live", "binding")
    assert replay.state["status"] == "STOPPED"
    assert replay.state["received_body_bytes"] == 7
    with pytest.raises(ValueError, match="BINDING"):
        m.Ledger(tmp_path / "live", "changed")


def test_no_approval_stops_before_contact_or_network(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(m, "PRIVATE", tmp_path)
    monkeypatch.setattr(m, "preflight", lambda: ({}, {}, "binding"))
    monkeypatch.setattr(
        m.sec, "private_contact", lambda *a: pytest.fail("approval must precede contact")
    )
    with pytest.raises(ValueError, match="EXPLICIT_APPROVAL"):
        m.run("acquire")
    assert not list(tmp_path.iterdir())


def test_wrong_authorization_scope_cannot_run(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(m, "PRIVATE", tmp_path)
    monkeypatch.setattr(m, "preflight", lambda: ({}, {}, "binding"))
    path = tmp_path / "stopped-feed-continuation-authorization-v1.json"
    path.write_text(
        json.dumps(
            {
                "approved": True,
                "registry_binding": "binding",
                "scope": "OTHER",
                "verbatim_user_approval": "yes",
            }
        )
    )
    with pytest.raises(ValueError, match="EXPLICIT_APPROVAL"):
        m.run("acquire")


def test_prior_global_attempts_must_remain_sealed(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(m, "PRIVATE", tmp_path)
    prior = {"old": 5, m.URL: 4}
    (tmp_path / "stopped-feed-continuation-prior-attempts-v1.json").write_text(json.dumps(prior))
    for counts in [prior, {**prior, m.URL: 5}]:
        (tmp_path / "scheduler-state.json").write_text(json.dumps({"attempts_by_url": counts}))
        m.verify_prior()
    for counts in [{**prior, "old": 4}, {**prior, m.URL: 6}, {**prior, "extra": 1}]:
        (tmp_path / "scheduler-state.json").write_text(json.dumps({"attempts_by_url": counts}))
        with pytest.raises(ValueError, match="PRIOR_ATTEMPTS"):
            m.verify_prior()


def test_reused_fragments_must_be_contiguous_and_hash_verified(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(m, "PRIVATE", tmp_path)
    monkeypatch.setattr(m.previous, "OUTPUT", tmp_path / "old")
    monkeypatch.setattr(m, "START", 12)
    monkeypatch.setattr(m.ranges, "PREFIX", 2)
    monkeypatch.setattr(m.ranges, "RANGE_CAP", 4)
    old = tmp_path / "old"
    old.mkdir()
    prefix = b"ab"
    digest = m.sec.sha(prefix)
    prefix_path = tmp_path / "repurchase-timing-diagnostics-v1/objects/sha256" / digest[:2] / digest
    prefix_path.parent.mkdir(parents=True)
    prefix_path.write_bytes(prefix)
    target = {
        "url": m.URL,
        "total_bytes": 20,
        "prefix_sha256": digest,
        "etag": '"etag"',
        "last_modified": "date",
    }
    entries = []
    for i, (start, end) in enumerate(m.ranges.ranges(20)[:3]):
        data = [b"cdef", b"ghij", b"kl"][i]
        path = old / f"range-{i}"
        path.write_bytes(data)
        headers = {
            "content-range": f"bytes {start}-{end}/20",
            "content-length": str(end - start + 1),
            "etag": '"etag"',
            "last-modified": "date",
            "content-type": "application/x-tar",
        }
        entry = {
            "url": m.URL,
            "start": start,
            "end": end,
            "status": 206,
            "headers": headers,
            "bytes": len(data),
        }
        if i < 2:
            entry.update(outcome="RANGE_CACHED", locator=path.name, sha256=m.sec.sha(data))
        else:
            entry.update(
                outcome="PARTIAL_SEARCH_TRUNCATED_OR_OVERSIZE_READ",
                partial_locator=path.name,
                partial_sha256=m.sec.sha(data),
            )
        entries.append(entry)
    state = {
        "status": "STOPPED",
        "stop_reason": "PARTIAL_SEARCH_TRUNCATED_OR_OVERSIZE_READ",
        "attempts": entries,
    }
    assert len(m.old_fragments(target, state)) == 4
    (old / "range-2").write_bytes(b"xx")
    with pytest.raises(ValueError, match="FRAGMENT_CHANGED"):
        m.old_fragments(target, state)


@pytest.mark.parametrize("denied", [False, True])
def test_one_request_completes_or_stops_on_denial(
    tmp_path: Path, monkeypatch: Any, denied: bool
) -> None:
    monkeypatch.setattr(m, "PRIVATE", tmp_path)
    monkeypatch.setattr(m, "OUTPUT", tmp_path / "output")
    target = {"url": m.URL, "filings": [{"accession": m.ACCESSION, "source_sha256": "a" * 64}]}
    monkeypatch.setattr(
        m,
        "preflight",
        lambda: (target, {"policy": {"dependencies": {}}, "registry": {}}, "binding"),
    )
    monkeypatch.setattr(m, "verify_prior", lambda: None)
    monkeypatch.setattr(m, "old_fragments", lambda *a: [])
    monkeypatch.setattr(m.sec, "private_contact", lambda *a: "synthetic contact")
    monkeypatch.setattr(m.sec, "install_firewall", lambda: None)
    monkeypatch.setattr(m.sec, "EXTENSION_FILES", m.sec.EXTENSION_FILES)
    (tmp_path / "stopped-feed-continuation-authorization-v1.json").write_text(
        json.dumps(
            {
                "approved": True,
                "registry_binding": "binding",
                "scope": "ONE_2011_RANGE_AFTER_STOP_ONLY",
                "verbatim_user_approval": "yes",
            }
        )
    )
    previous_output = tmp_path / "old"
    previous_output.mkdir()
    (previous_output / "state.json").write_text("{}")
    monkeypatch.setattr(m.previous, "OUTPUT", previous_output)

    class Scheduler:
        def __init__(self, root: Path) -> None:
            self.count = 4

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *a: Any) -> None:
            pass

        def attempts_used(self, url: str) -> int:
            return self.count

        def start(self, deadline: float) -> None:
            pass

        def record_attempt(self, url: str) -> None:
            self.count += 1

        def block(self, reason: str) -> None:
            pass

        def hold(self, seconds: float, reason: str) -> None:
            pass

    class Cache:
        def __init__(self, root: Path) -> None:
            pass

        def read(self, digest: str) -> bytes:
            return b"source"

    def fake_receive(t: Any, start: int, end: int, agent: str, ledger: Any, ctx: Any) -> None:
        attempt = ledger.state["attempts"][-1]
        if denied:
            attempt["status"] = 403
            raise ValueError("TWO_ARCHIVE_HTTP_403_STOP")
        raw = b"abc"
        ledger.charge(len(raw))
        path = ledger.root / "synthetic"
        path.write_bytes(raw)
        attempt.update(status=206, outcome="RANGE_CACHED", locator=path.name, sha256=m.sec.sha(raw))
        ledger.save()

    monkeypatch.setattr(m.sec, "Scheduler", Scheduler)
    monkeypatch.setattr(m.sec, "Cache", Cache)
    monkeypatch.setattr(m.previous, "receive", fake_receive)
    monkeypatch.setattr(m.scanner, "scan", lambda *a: {"matches": [{"accession": m.ACCESSION}]})
    result = m.run("acquire")
    assert result["HTTP_attempts"] == 1
    assert result["status"] == (
        "STOPPED" if denied else "COMPLETED_PARTIAL_MEMBERSHIP_NOT_CLOCK_CERTIFICATION"
    )
    assert result["public_times_certified"] == 0
