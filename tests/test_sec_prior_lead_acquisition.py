"""Protect exact lead scope, complete submissions, budgets and stop behavior."""

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
    "prior_source_test", ROOT / "tools/corporate_events/sec_prior_lead_acquisition.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def entries() -> list[dict[str, Any]]:
    return [
        {
            "url": m.URLS[0],
            "accession": "0000902664-07-002360",
            "filed": "2007-07-26",
            "form": "8-K",
            "index_filer_cik": "350199",
        },
        {
            "url": m.URLS[1],
            "accession": "0000902664-07-002366",
            "filed": "2007-07-26",
            "form": "8-K",
            "index_filer_cik": "350199",
        },
        {
            "url": m.URLS[2],
            "accession": "0001104659-08-047772",
            "filed": "2008-07-25",
            "form": "8-K",
            "index_filer_cik": "896985",
        },
    ]


def submission(e: dict[str, Any]) -> bytes:
    return (
        f"<SEC-DOCUMENT>example\n<SEC-HEADER>\nACCESSION NUMBER: {e['accession']}\n"
        f"CONFORMED SUBMISSION TYPE: 8-K\nFILED AS OF DATE: {e['filed'].replace('-', '')}\n"
        f"CENTRAL INDEX KEY: {e['index_filer_cik']}\n</SEC-HEADER>\n<DOCUMENT>\n"
        "<TYPE>8-K\n<TEXT>example</TEXT>\n</DOCUMENT>\n</SEC-DOCUMENT>\n"
    ).encode()


def test_exact_manifest_required() -> None:
    rows = entries()
    m.validate_manifest(rows)
    rows[0]["url"] = "https://example.com/x"
    with pytest.raises(ValueError, match="EXACT_URLS"):
        m.validate_manifest(rows)


@pytest.mark.parametrize("change", ["accession", "form", "date", "cik", "truncated", "primary"])
def test_submission_identity_and_completeness(change: str) -> None:
    e = entries()[0]
    raw = submission(e)
    proof = m.check_submission(raw, e)
    assert (
        proof["index_filer_cik_matched"]
        and not proof["issuer_role_verified"]
        and not proof["public_time_certified"]
    )
    if change == "accession":
        raw = raw.replace(e["accession"].encode(), b"0000902664-07-999999")
    elif change == "form":
        raw = raw.replace(b"CONFORMED SUBMISSION TYPE: 8-K", b"CONFORMED SUBMISSION TYPE: 10-Q")
    elif change == "date":
        raw = raw.replace(b"20070726", b"20070727")
    elif change == "cik":
        raw = raw.replace(b"350199", b"999999")
    elif change == "truncated":
        raw = raw.replace(b"</SEC-DOCUMENT>", b"")
    else:
        raw = raw.replace(b"<TYPE>8-K", b"<TYPE>10-Q")
    with pytest.raises(ValueError):
        m.check_submission(raw, e)


@pytest.mark.parametrize("status", [200, 301, 403, 429, 500])
def test_http_policy_no_redirects_and_denials(status: int) -> None:
    pairs = [("Content-Length", "7"), ("Content-Type", "text/plain")]
    if status == 200:
        assert m.checked_headers(status, pairs) == 7
    else:
        with pytest.raises(ValueError, match=f"HTTP_{status}"):
            m.checked_headers(status, pairs)


@pytest.mark.parametrize(
    "pairs",
    [
        [("Content-Length", "0"), ("Content-Type", "text/plain")],
        [("Content-Length", "8388609"), ("Content-Type", "text/plain")],
        [("Content-Length", "5"), ("Content-Length", "6"), ("Content-Type", "text/plain")],
        [("Content-Length", "5"), ("Content-Type", "text/html")],
        [("Content-Length", "5"), ("Content-Type", "text/plain"), ("Content-Encoding", "gzip")],
        [("Content-Length", "5"), ("Content-Type", "text/plain"), ("Transfer-Encoding", "chunked")],
    ],
)
def test_oversize_duplicate_or_unsupported_encoding_rejected(pairs: list[tuple[str, str]]) -> None:
    with pytest.raises(ValueError):
        m.checked_headers(200, pairs)


def test_ledger_cannot_repeat_or_exceed_response_cap(tmp_path: Path) -> None:
    ledger = m.Ledger(tmp_path / "live", "binding")
    ledger.reserve(m.URLS[0])
    ledger.charge(7)
    with pytest.raises(ValueError, match="ATTEMPT_FORBIDDEN"):
        ledger.reserve(m.URLS[0])
    with pytest.raises(ValueError, match="BYTE_CAP"):
        ledger.charge(m.CAP)
    ledger.stop("SYNTHETIC_FAILURE")
    again = m.Ledger(tmp_path / "live", "binding")
    assert again.state["status"] == "STOPPED" and again.state["received_body_bytes"] == 7


def test_original_stop_reason_survives_state_write_failure(
    tmp_path: Path, monkeypatch: Any
) -> None:
    ledger = m.Ledger(tmp_path / "live", "binding")

    def fail(reason: str) -> None:
        raise PermissionError("synthetic state error")

    monkeypatch.setattr(ledger, "stop", fail)
    with pytest.raises(ValueError, match="STATE_WRITE_INCOMPLETE_ORIGINAL_ORIGINAL_REASON"):
        m.safe_stop(ledger, "ORIGINAL_REASON")
    incident = json.loads((ledger.root / "state-write-incident.json").read_bytes())
    assert incident["original_stop_reason"] == "ORIGINAL_REASON"
    assert incident["original_state_or_pending_preserved"]


def test_incident_record_failure_does_not_mask_original_stop(
    tmp_path: Path, monkeypatch: Any
) -> None:
    ledger = m.Ledger(tmp_path / "live", "binding")

    def fail_stop(reason: str) -> None:
        raise PermissionError("synthetic state error")

    def fail_write(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic incident-write error")

    monkeypatch.setattr(ledger, "stop", fail_stop)
    monkeypatch.setattr(m.sec, "write_new", fail_write)
    with pytest.raises(ValueError, match="STATE_WRITE_INCOMPLETE_ORIGINAL_ORIGINAL_REASON"):
        m.safe_stop(ledger, "ORIGINAL_REASON")


def test_unapproved_acquire_stops_before_contact_or_network(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(m, "PRIVATE", tmp_path)
    monkeypatch.setattr(m, "preflight", lambda: ({}, "binding"))
    monkeypatch.setattr(m.sec, "private_contact", lambda *a: pytest.fail("approval first"))
    with pytest.raises(ValueError, match="EXPLICIT_APPROVAL"):
        m.run("acquire")
    assert not list(tmp_path.iterdir())


class Reply:
    def __init__(self, raw: bytes, status: int = 200, interrupt: bool = False) -> None:
        self.raw = raw
        self.status = status
        self.interrupt = interrupt
        self.offset = 0
        self.read_called = False

    def getheaders(self) -> list[tuple[str, str]]:
        return [("Content-Length", str(len(self.raw))), ("Content-Type", "text/plain")]

    def read(self, n: int) -> bytes:
        self.read_called = True
        if self.interrupt:
            raise http.client.IncompleteRead(self.raw[:7], n)
        out = self.raw[self.offset : self.offset + n]
        self.offset += len(out)
        return out

    def isclosed(self) -> bool:
        return self.offset == len(self.raw)


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


@pytest.mark.parametrize("mode", ["success", "partial", "denied"])
def test_transport_charges_bytes_and_never_reads_denial(tmp_path: Path, mode: str) -> None:
    e = entries()[0]
    raw = submission(e)
    ledger = m.Ledger(tmp_path / "live", "binding")
    ledger.reserve(e["url"])
    reply = Reply(raw, status=403 if mode == "denied" else 200, interrupt=mode == "partial")
    conn = Connection(reply)
    if mode == "success":
        m.receive(
            e, "synthetic contact", ledger, ssl.create_default_context(), lambda *a, **k: conn
        )
        a = ledger.state["attempts"][0]
        assert a["outcome"] == "SOURCE_VALIDATED"
        assert m.sec.Cache(ledger.root).read(a["sha256"]) == raw
        assert a["bytes"] == len(raw)
    else:
        with pytest.raises(ValueError):
            m.receive(
                e, "synthetic contact", ledger, ssl.create_default_context(), lambda *a, **k: conn
            )
        assert ledger.state["received_body_bytes"] == (7 if mode == "partial" else 0)
        assert reply.read_called is (mode == "partial")
    assert conn.closed and len(conn.calls) == 1 and m.sec.NETWORK_HOST.get() is None
