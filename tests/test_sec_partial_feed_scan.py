"""Exercise partial archive boundaries, membership identity and bounded streaming."""

from __future__ import annotations

import gzip
import importlib.util
import io
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "partial_scan_test", ROOT / "tools/corporate_events/sec_partial_feed_scan.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
ACC = "0000000001-10-000001"


def archive(tmp_path: Path, body: bytes, *, name: str | None = None) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        info = tarfile.TarInfo(name or "./" + ACC + ".nc")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    return gzip.compress(raw.getvalue(), mtime=0)


def body(acc: str = ACC) -> bytes:
    return f"<SUBMISSION>\n<ACCESSION-NUMBER>{acc}\n<DOCUMENT>\nexample\n".encode()


def test_exact_target_found_across_fragments_without_claiming_public_time(tmp_path: Path) -> None:
    raw = archive(tmp_path, body())
    cut = len(raw) // 2
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_bytes(raw[:cut])
    second.write_bytes(raw[cut:])
    result = m.scan([first, second], {ACC: body()})
    assert result["status"] == "ALL_TARGET_BODIES_LOCATED_IN_PREFIX"
    assert result["matches"][0]["source_bytes_identical"]
    assert result["matches"][0]["tar_header_checksum_verified"]
    assert result["gzip_eof_and_checksum_verified"] is False
    assert result["absence_inference_allowed"] is False
    assert result["public_times_certified"] == 0


def test_truncated_prefix_cannot_prove_absence(tmp_path: Path) -> None:
    path = tmp_path / "prefix"
    path.write_bytes(archive(tmp_path, body())[:20])
    result = m.scan([path], {ACC: body()})
    assert result["status"] == "PREFIX_EXHAUSTED"
    assert result["unlocated_accessions"] == [ACC]
    assert not result["absence_inference_allowed"]


def test_wrong_internal_accession_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "raw"
    path.write_bytes(archive(tmp_path, body("0000000002-10-000001")))
    with pytest.raises(ValueError, match="INTERNAL_ACCESSION"):
        m.scan([path], {ACC: body()})


def test_exact_member_name_required(tmp_path: Path) -> None:
    path = tmp_path / "raw"
    path.write_bytes(archive(tmp_path, body(), name="other/" + ACC + ".nc"))
    result = m.scan([path], {ACC: body()})
    assert not result["matches"] and result["unlocated_accessions"] == [ACC]


def test_changed_body_and_invalid_scope_are_not_hidden(tmp_path: Path) -> None:
    path = tmp_path / "raw"
    path.write_bytes(archive(tmp_path, body()))
    assert not m.scan([path], {ACC: body() + b"different"})["matches"][0]["source_bytes_identical"]
    with pytest.raises(ValueError, match="SCOPE"):
        m.scan([path], {"bad": body()})


def test_corrupt_gzip_rejected(tmp_path: Path) -> None:
    path = tmp_path / "raw"
    path.write_bytes(b"not a gzip file")
    with pytest.raises(ValueError, match="GZIP_INVALID"):
        m.scan([path], {ACC: body()})


def test_limits_apply_without_reading_large_files(tmp_path: Path, monkeypatch: Any) -> None:
    path = tmp_path / "raw"
    path.write_bytes(archive(tmp_path, body()))
    monkeypatch.setattr(m, "MAX_COMPRESSED", 2)
    with pytest.raises(ValueError, match="COMPRESSED_LIMIT"):
        m.scan([path], {ACC: body()})


def test_one_match_cannot_satisfy_two_targets(tmp_path: Path) -> None:
    path = tmp_path / "raw"
    path.write_bytes(archive(tmp_path, body()))
    other = "0000000002-10-000001"
    result = m.scan([path], {ACC: body(), other: body(other)})
    assert [item["accession"] for item in result["matches"]] == [ACC]
    assert result["unlocated_accessions"] == [other]
    assert result["status"] != "ALL_TARGET_BODIES_LOCATED_IN_PREFIX"
    assert not result["absence_inference_allowed"]


def test_bad_tar_header_checksum_is_rejected(tmp_path: Path) -> None:
    raw = bytearray(gzip.decompress(archive(tmp_path, body())))
    raw[0] ^= 1
    path = tmp_path / "raw"
    path.write_bytes(gzip.compress(raw, mtime=0))
    with pytest.raises(ValueError, match="TAR_HEADER_INVALID"):
        m.scan([path], {ACC: body()})


def test_component_comparison_is_target_only(tmp_path: Path) -> None:
    path = tmp_path / "raw"
    path.write_bytes(archive(tmp_path, body()))
    calls: list[tuple[bytes, bytes]] = []

    def compare(source: bytes, found: bytes) -> dict[str, Any]:
        calls.append((source, found))
        return {"synthetic_match": source == found}

    result = m.scan([path], {ACC: body()}, compare)
    assert calls == [(body(), body())]
    assert result["matches"][0]["component_comparison"] == {"synthetic_match": True}
