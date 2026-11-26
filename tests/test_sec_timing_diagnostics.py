"""Exercise exact targets, persistent budgets and interrupted archive-prefix responses."""

from __future__ import annotations

import copy
import gzip
import http.client
import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "timing_test", ROOT / "tools/corporate_events/sec_timing_diagnostics.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
URLS = [
    f"https://www.sec.gov/Archives/edgar/Feed/{y}/QTR{q}/{d}.nc.tar.gz"
    for y, q, d in [
        (2007, 3, "20070802"),
        (2008, 3, "20080730"),
        (2010, 3, "20100928"),
        (2011, 4, "20111130"),
    ]
]


def plan() -> dict[str, Any]:
    return {
        "targets": [
            {
                "url": url,
                "range": "bytes=0-1048575",
                "target_accessions": [f"0000000001-{i + 6:02}-000001"]
                + (["0000000002-11-000001"] if i == 3 else []),
            }
            for i, url in enumerate(URLS)
        ],
        "limits": {
            "maximum_HTTP_requests_including_failures": 4,
            "maximum_attempts_per_URL": 1,
            "maximum_received_body_bytes_total": m.TOTAL,
            "maximum_received_body_bytes_per_response": m.CAP,
            "maximum_decoded_bytes_per_prefix": 33_554_432,
            "minimum_request_start_spacing_seconds": 0.5,
            "concurrency": 1,
            "socket_timeout_seconds": 30,
            "redirects_allowed": False,
            "automatic_retries_allowed": False,
            "whole_archive_download_allowed": False,
            "old_budget_resets_allowed": False,
        },
    }


def headers(length: int = 10, total: int | None = None) -> dict[str, str]:
    return {
        "content-range": f"bytes 0-{length - 1}/{total or length}",
        "content-length": str(length),
        "content-type": "application/gzip",
    }


def ledger(tmp_path: Path) -> Any:
    return m.Ledger(tmp_path / "diagnostics", "frozen-binding", URLS)


class Reply:
    def __init__(
        self, body: bytes, status: int = 206, fields: Any = None, interrupted: bool = False
    ) -> None:
        self.body, self.status = body, status
        self.fields = list((fields or headers(len(body))).items())
        self.offset = 0
        self.read_sizes: list[int] = []
        self.interrupted = interrupted

    def getheaders(self) -> Any:
        return self.fields

    def read(self, count: int) -> bytes:
        self.read_sizes.append(count)
        if self.interrupted:
            self.interrupted = False
            self.offset = len(self.body)
            raise http.client.IncompleteRead(self.body, count)
        result = self.body[self.offset : self.offset + count]
        self.offset += len(result)
        return result

    def isclosed(self) -> bool:
        return self.offset == len(self.body)


class Connection:
    def __init__(self, reply: Reply) -> None:
        self.reply, self.closed = reply, False
        self.requests: list[Any] = []

    def request(self, *args: Any, **kwargs: Any) -> None:
        self.requests.append((args, kwargs))

    def getresponse(self) -> Reply:
        return self.reply

    def close(self) -> None:
        self.closed = True


def transport(reply: Reply) -> tuple[Any, Connection]:
    conn = Connection(reply)
    return m.Transport(None, factory=lambda *a, **k: conn), conn


def test_plan_has_exact_frozen_url_range_and_budget_scope() -> None:
    assert len(m.validate_plan(plan())) == 4
    assert m.checked_headers(206, headers()) == 10


@pytest.mark.parametrize(
    "change",
    [
        "host",
        "query",
        "quarter",
        "range",
        "duplicate",
        "accession",
        "retry",
        "bytes",
        "request_cap",
    ],
)
def test_plan_cannot_be_broadened(change: str) -> None:
    value = plan()
    target = value["targets"][0]
    if change == "host":
        target["url"] = target["url"].replace("www.sec.gov", "other.example")
    elif change == "query":
        target["url"] += "?variant=1"
    elif change == "quarter":
        target["url"] = target["url"].replace("QTR3", "QTR2")
    elif change == "range":
        target["range"] = "bytes=0-2097151"
    elif change == "duplicate":
        value["targets"][1] = copy.deepcopy(target)
    elif change == "accession":
        target["target_accessions"] = ["invalid"]
    elif change == "retry":
        value["limits"]["automatic_retries_allowed"] = True
    elif change == "bytes":
        value["limits"]["maximum_received_body_bytes_total"] += 1
    else:
        value["limits"]["maximum_HTTP_requests_including_failures"] = 5
    with pytest.raises(ValueError):
        m.validate_plan(value)


@pytest.mark.parametrize("status", [200, 302, 403, 404, 429, 500])
def test_non_range_statuses_stop_before_reading_body(tmp_path: Path, status: int) -> None:
    book = ledger(tmp_path)
    book.reserve(URLS[0])
    reply = Reply(b"not-an-archive", status=status)
    client, conn = transport(reply)
    with pytest.raises(ValueError):
        client.get(plan()["targets"][0], "synthetic-contact", book)
    assert reply.read_sizes == [] and book.state["received_body_bytes"] == 0
    assert book.state["attempts"][0]["http_status"] == status
    assert conn.closed and m.sec.NETWORK_HOST.get() is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("content-range", "bytes 1-10/50"),
        ("content-range", "bytes 0-9/*"),
        ("content-range", "bytes 0-9/50"),
        ("content-length", "11"),
        ("content-encoding", "gzip"),
        ("transfer-encoding", "chunked"),
        ("content-type", "text/html"),
    ],
)
def test_invalid_framing_is_rejected(field: str, value: str) -> None:
    fields = headers()
    fields[field] = value
    with pytest.raises(ValueError):
        m.checked_headers(206, fields)


def test_one_megabyte_limit_is_never_probed_with_an_extra_byte(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    book.reserve(URLS[0])
    reply = Reply(b"x" * m.CAP, fields=headers(m.CAP, total=2 * m.CAP))
    client, conn = transport(reply)
    assert len(client.get(plan()["targets"][0], "synthetic-contact", book)) == m.CAP
    assert sum(reply.read_sizes) == m.CAP and max(reply.read_sizes) <= 65536
    assert book.state["received_body_bytes"] == m.CAP
    assert conn.requests[0][1]["headers"]["Range"] == "bytes=0-1048575"
    assert "synthetic-contact" not in book.path.read_text()


def test_interrupted_bytes_are_charged_cached_and_not_retried(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    book.reserve(URLS[0])
    reply = Reply(b"partial", fields=headers(50), interrupted=True)
    client, _ = transport(reply)
    with pytest.raises(ValueError, match="PARTIAL_RESPONSE"):
        client.get(plan()["targets"][0], "synthetic-contact", book)
    book.finish(client.body, "TIMING_PARTIAL_RESPONSE_STOP", [])
    book.stop("TIMING_PARTIAL_RESPONSE_STOP")
    assert m.audit(book)["received_body_bytes"] == 7
    reopened = ledger(tmp_path)
    with pytest.raises(ValueError, match="NO_RESET_OR_RETRY"):
        reopened.reserve(URLS[0])
    assert len(reopened.state["attempts"]) == 1


def test_clean_eof_before_declared_length_is_also_charged(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    book.reserve(URLS[0])
    reply = Reply(b"abc", fields=headers(10))
    client, _ = transport(reply)
    with pytest.raises(ValueError, match="TRUNCATED"):
        client.get(plan()["targets"][0], "synthetic-contact", book)
    assert client.body == b"abc" and book.state["received_body_bytes"] == 3


def test_duplicate_framing_headers_stop_before_read(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    book.reserve(URLS[0])
    reply = Reply(b"abc")
    reply.fields.append(("Content-Length", "999"))
    client, _ = transport(reply)
    with pytest.raises(ValueError, match="DUPLICATE_FRAMING"):
        client.get(plan()["targets"][0], "synthetic-contact", book)
    assert not reply.read_sizes


def test_constructor_failure_does_not_leave_network_capability_set(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    book.reserve(URLS[0])

    def fail(*args: Any, **kwargs: Any) -> None:
        raise OSError("synthetic failure")

    client = m.Transport(None, factory=fail)
    with pytest.raises(OSError):
        client.get(plan()["targets"][0], "synthetic-contact", book)
    assert m.sec.NETWORK_HOST.get() is None


def test_transport_requires_a_prior_exact_url_reservation(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    reply = Reply(b"abc")
    client, conn = transport(reply)
    with pytest.raises(ValueError):
        client.get(plan()["targets"][0], "synthetic-contact", book)
    assert not conn.requests


def test_ledger_has_no_budget_reset_or_fifth_request(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    for url in URLS:
        book.reserve(url)
        book.charge(1)
        book.finish(b"x", "SYNTHETIC_REJECTED_PREFIX", [])
    assert len(book.state["attempts"]) == 4
    with pytest.raises(ValueError):
        book.reserve(URLS[0])
    with pytest.raises(ValueError, match="IDENTITY_CHANGED"):
        m.Ledger(book.root, "different-binding", URLS)


def test_deleted_ledger_or_incomplete_atomic_write_stops_reuse(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    pending = book.path.with_suffix(".pending")
    pending.write_bytes(b"incomplete")
    with pytest.raises(ValueError, match="INCOMPLETE_STATE"):
        ledger(tmp_path)
    pending.unlink()
    book.path.unlink()
    (book.root / "old-receipt.json").write_text("{}")
    with pytest.raises(ValueError, match="NONEMPTY_NAMESPACE"):
        ledger(tmp_path)


def test_inconsistent_persisted_byte_total_cannot_be_reused(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    state = json.loads(book.path.read_bytes())
    state["received_body_bytes"] = 100
    book.path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="LEDGER_INVARIANT"):
        ledger(tmp_path)


def test_target_header_match_does_not_certify_public_time(tmp_path: Path) -> None:
    acc = plan()["targets"][0]["target_accessions"][0]
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        info = tarfile.TarInfo(acc + ".nc")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    body = gzip.compress(stream.getvalue(), mtime=0)
    proofs = m.prefix_proofs(body, [acc])
    assert proofs[0]["target_member"] is not None
    book = ledger(tmp_path)
    book.reserve(URLS[0])
    book.charge(len(body))
    book.finish(body, "PREFIX_INSPECTED", proofs)
    book.stop("SYNTHETIC_STOP")
    before = book.path.read_bytes()
    summary = m.audit(book)
    assert summary["target_header_matches"] == 1 and summary["public_times_certified"] == 0
    assert not summary["archive_day_semantics_certified"]
    assert book.path.read_bytes() == before
    assert not any(summary["firewall_counters"].values())


def test_ledger_does_not_hide_a_reported_overrun(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    book.reserve(URLS[0])
    with pytest.raises(ValueError, match="BYTE_BUDGET_VIOLATION"):
        book.charge(m.CAP + 1)
    assert book.state["received_body_bytes"] == m.CAP + 1
    assert book.state["status"] == "STOPPED"
    with pytest.raises(ValueError):
        m.audit(book)


def test_private_or_verbose_errors_are_reduced_to_safe_reason_codes() -> None:
    assert m.safe_reason(ValueError("a synthetic secret string")) == "TIMING_VALUEERROR"
    assert (
        m.safe_reason(ValueError("TIMING_CONTENT_RANGE_INVALID")) == "TIMING_CONTENT_RANGE_INVALID"
    )
