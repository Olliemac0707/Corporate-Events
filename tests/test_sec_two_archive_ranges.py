"""Exercise source identity, bounded transfers, durable stops and archive integrity offline."""

from __future__ import annotations

import http.client
import importlib.util
import io
import ssl
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "two_archive_test", ROOT / "tools/corporate_events/sec_two_archive_ranges.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def target(index: int = 0) -> dict[str, Any]:
    return {
        "url": m.URLS[index],
        "total_bytes": m.SIZES[index],
        "accession": m.ACCESSIONS[index],
        "etag": '"a1b2-20"',
        "last_modified": "Thu, 23 Nov 2017 01:14:32 GMT",
        "prefix_sha256": "a" * 64,
        "source_sha256": "b" * 64,
    }


def headers(t: dict[str, Any], start: int, end: int) -> list[tuple[str, str]]:
    return [
        ("Content-Range", f"bytes {start}-{end}/{t['total_bytes']}"),
        ("Content-Length", str(end - start + 1)),
        ("ETag", t["etag"]),
        ("Last-Modified", t["last_modified"]),
        ("Content-Type", "application/x-tar"),
    ]


def test_plan_matches_frozen_limits() -> None:
    m.validate_targets([target(0), target(1)])
    assert sum(t["total_bytes"] - m.PREFIX for t in [target(0), target(1)]) == m.TOTAL_CAP
    for t in [target(0), target(1)]:
        spans = m.ranges(t["total_bytes"])
        assert len(spans) == 4
        assert spans[0][0] == m.PREFIX and spans[-1][1] == t["total_bytes"] - 1
        assert all(end - start + 1 <= m.RANGE_CAP for start, end in spans)
        assert all(spans[i][1] + 1 == spans[i + 1][0] for i in range(3))


@pytest.mark.parametrize(
    "key,value",
    [
        ("url", "https://example.com"),
        ("total_bytes", 1),
        ("accession", "0000000000-00-000000"),
        ("etag", 'W/"weak"'),
        ("prefix_sha256", "short"),
    ],
)
def test_plan_rejects_changed_scope(key: str, value: Any) -> None:
    targets = [target(0), target(1)]
    targets[0][key] = value
    with pytest.raises(ValueError):
        m.validate_targets(targets)


@pytest.mark.parametrize("status", [200, 301, 403, 404, 429, 500])
def test_http_errors_and_fallbacks_are_rejected(status: int) -> None:
    t = target()
    start, end = m.ranges(t["total_bytes"])[0]
    with pytest.raises(ValueError, match=f"HTTP_{status}"):
        m.checked_headers(status, headers(t, start, end), t, start, end)


@pytest.mark.parametrize(
    "field,value",
    [
        ("Content-Range", "bytes 0-10/11"),
        ("Content-Length", "4"),
        ("ETag", '"changed"'),
        ("Last-Modified", "changed"),
        ("Content-Type", "text/html"),
        ("Content-Encoding", "gzip"),
        ("Transfer-Encoding", "chunked"),
    ],
)
def test_representation_change_stops(field: str, value: str) -> None:
    t = target()
    start, end = m.ranges(t["total_bytes"])[0]
    h = [(k, v) for k, v in headers(t, start, end) if k != field] + [(field, value)]
    with pytest.raises(ValueError):
        m.checked_headers(206, h, t, start, end)


def test_duplicate_headers_are_rejected() -> None:
    t = target()
    start, end = m.ranges(t["total_bytes"])[0]
    with pytest.raises(ValueError, match="DUPLICATE"):
        m.checked_headers(206, headers(t, start, end) + [("content-length", "1")], t, start, end)


def test_ledger_forbids_reuse_and_persists_stop(tmp_path: Path) -> None:
    ledger = m.Ledger(tmp_path / "live", "frozen")
    start, end = m.ranges(m.SIZES[0])[0]
    ledger.reserve(m.URLS[0], start, end)
    ledger.charge(7)
    with pytest.raises(ValueError, match="ATTEMPT_FORBIDDEN"):
        ledger.reserve(m.URLS[0], start, end)
    ledger.stop("SYNTHETIC_FAILURE")
    replay = m.Ledger(tmp_path / "live", "frozen")
    assert replay.state["received_body_bytes"] == 7
    assert replay.state["status"] == "STOPPED"
    with pytest.raises(ValueError, match="ATTEMPT_FORBIDDEN"):
        replay.reserve(m.URLS[0], *m.ranges(m.SIZES[0])[1])
    with pytest.raises(ValueError, match="BINDING"):
        m.Ledger(tmp_path / "live", "changed")


def test_ledger_byte_cap_is_durable(tmp_path: Path) -> None:
    ledger = m.Ledger(tmp_path / "live", "frozen")
    ledger.reserve(m.URLS[0], *m.ranges(m.SIZES[0])[0])
    with pytest.raises(ValueError, match="BYTE_CAP"):
        ledger.charge(m.RANGE_CAP + 1)
    assert m.Ledger(tmp_path / "live", "frozen").state["status"] == "STOPPED"


class Reply:
    def __init__(self, t: dict[str, Any], start: int, end: int, status: int = 206) -> None:
        self.status, self.headers = status, headers(t, start, end)
        self.read_called = False

    def getheaders(self) -> list[tuple[str, str]]:
        return self.headers

    def read(self, n: int) -> bytes:
        self.read_called = True
        raise http.client.IncompleteRead(b"partial", n)

    def isclosed(self) -> bool:
        return True


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


@pytest.mark.parametrize("status", [206, 403, 429, 200])
def test_transport_charges_partial_and_never_reads_denials(tmp_path: Path, status: int) -> None:
    t = target()
    start, end = m.ranges(t["total_bytes"])[0]
    ledger = m.Ledger(tmp_path / "live", "frozen")
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
            lambda *args, **kwargs: conn,
        )
    assert ledger.state["received_body_bytes"] == (7 if status == 206 else 0)
    assert reply.read_called is (status == 206)
    if status == 206:
        assert (tmp_path / "live/range-01.part").read_bytes() == b"partial"
    assert conn.requests[0][1]["headers"]["If-Range"] == t["etag"]
    assert conn.closed and m.sec.NETWORK_HOST.get() is None


def fixture(
    tmp_path: Path, names: list[str], *, source: bytes | None = None
) -> tuple[Path, Any, bytes]:
    accession = m.ACCESSIONS[0]
    raw = (
        source if source is not None else f"<ACCESSION-NUMBER>{accession}\n<DOCUMENT>text".encode()
    )
    t = target()
    t["source_sha256"] = m.sec.sha(raw)
    archive_path = tmp_path / "fixture.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for name in names:
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    return archive_path, t, raw


def test_complete_gzip_tar_and_accession_identity(tmp_path: Path) -> None:
    path, t, raw = fixture(tmp_path, ["unrelated.nc", m.ACCESSIONS[0] + ".nc"])
    result = m.inspect(path, t, raw)
    assert result["target_found"] and result["gzip_eof_and_checksum_verified"]
    assert result["matches"][0]["exact_raw_filing_match"]
    assert result["unrelated_filing_bodies_extracted"] == result["public_times_certified"] == 0


def test_absent_target_and_different_content_are_preserved(tmp_path: Path) -> None:
    path, t, raw = fixture(tmp_path, ["unrelated.nc"])
    assert not m.inspect(path, t, raw)["target_found"]
    path, t, raw = fixture(tmp_path, [m.ACCESSIONS[0] + ".nc"])
    alt = raw + b"different"
    t["source_sha256"] = m.sec.sha(alt)
    assert not m.inspect(path, t, alt)["matches"][0]["exact_raw_filing_match"]


@pytest.mark.parametrize("kind", ["truncated", "corrupt", "trailing"])
def test_gzip_integrity_failure_is_not_evidence(tmp_path: Path, kind: str) -> None:
    path, t, raw = fixture(tmp_path, [m.ACCESSIONS[0] + ".nc"])
    data = path.read_bytes()
    if kind == "truncated":
        data = data[:-8]
    elif kind == "corrupt":
        data = data[:-8] + bytes([data[-8] ^ 1]) + data[-7:]
    else:
        data += b"unverified"
    path.write_bytes(data)
    with pytest.raises(ValueError):
        m.inspect(path, t, raw)


def test_wrong_internal_accession_and_duplicates_fail(tmp_path: Path) -> None:
    path, t, raw = fixture(tmp_path, [m.ACCESSIONS[0] + ".nc"], source=b"no accession")
    with pytest.raises(ValueError, match="ACCESSION"):
        m.inspect(path, t, raw)
    path, t, raw = fixture(tmp_path, [m.ACCESSIONS[0] + ".nc"] * 2)
    with pytest.raises(ValueError, match="DUPLICATE"):
        m.inspect(path, t, raw)


def test_successful_range_cached_and_not_repeated(tmp_path: Path) -> None:
    t = target()
    start, end = m.ranges(t["total_bytes"])[-1]
    ledger = m.Ledger(tmp_path / "live", "frozen")
    ledger.reserve(t["url"], start, end)

    class CompleteReply(Reply):
        def __init__(self) -> None:
            super().__init__(t, start, end)
            self.remaining: int = int(end - start + 1)

        def read(self, n: int) -> bytes:
            count = min(n, self.remaining)
            self.remaining -= count
            return b"z" * count

        def isclosed(self) -> bool:
            return self.remaining == 0

    conn = Connection(CompleteReply())
    context = ssl.create_default_context()
    m.receive(t, start, end, "synthetic contact", ledger, context, lambda *a, **k: conn)
    attempt = ledger.state["attempts"][0]
    cached = tmp_path / "live" / attempt["locator"]
    assert attempt["outcome"] == "RANGE_CACHED"
    assert cached.stat().st_size == attempt["bytes"] == end - start + 1
    assert m.sha_file(cached) == attempt["sha256"]
    with pytest.raises(ValueError, match="UNRESERVED"):
        m.receive(t, start, end, "synthetic contact", ledger, context, lambda *a, **k: conn)


def test_assembly_requires_contiguous_hashed_ranges(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(m, "PREFIX", 2)
    monkeypatch.setattr(m, "RANGE_CAP", 4)
    monkeypatch.setattr(m, "SIZES", [10, m.SIZES[1]])
    t = target()
    t["total_bytes"] = 10
    t["prefix_sha256"] = m.sec.sha(b"ab")
    ledger = m.Ledger(tmp_path / "live", "frozen")
    for index, block in enumerate([b"cdef", b"ghij"]):
        start, end = m.ranges(10)[index]
        ledger.reserve(t["url"], start, end)
        ledger.charge(len(block))
        path = ledger.root / f"range-{index}"
        path.write_bytes(block)
        ledger.state["attempts"][-1].update(
            outcome="RANGE_CACHED", sha256=m.sec.sha(block), locator=path.name
        )
        ledger.save()
    result = m.assemble(t, ledger, b"ab", tmp_path / "inspection")
    assert result.read_bytes() == b"abcdefghij"
    (ledger.root / "range-1").write_bytes(b"XXXX")
    with pytest.raises(ValueError, match="CACHED_RANGE_CHANGED"):
        m.assemble(t, ledger, b"ab", tmp_path / "other")
