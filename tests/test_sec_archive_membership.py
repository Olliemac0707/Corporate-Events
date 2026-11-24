"""Check cached-prefix bounds, exact identities and corrupt archives."""

from __future__ import annotations

import gzip
import importlib.util
import io
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest

spec = importlib.util.spec_from_file_location(
    "archive_inspector_tests",
    Path(__file__).resolve().parents[1] / "tools/corporate_events/sec_archive_membership.py",
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
ACC = "0000066740-22-000061"


def archive(name: str, body: bytes = b"source", *, kind: bytes = tarfile.REGTYPE) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as writer:
        info = tarfile.TarInfo(name)
        info.type = kind
        info.size = len(body) if kind == tarfile.REGTYPE else 0
        writer.addfile(info, io.BytesIO(body))
    return gzip.compress(raw.getvalue(), mtime=0)


def test_chunked_exact_member_with_header_proof() -> None:
    raw = archive("./" + ACC + ".nc")
    result = m.inspect_prefix([raw[:20], raw[20:]], ACC)
    assert result["target_member"]["header_byte_start"] == 0
    assert len(result["target_member"]["header_sha256"]) == 64
    assert result["target_member"]["member_body_complete_in_prefix"]
    assert result["gzip_eof_and_checksum_available"]
    assert not result["public_time_certified"]


@pytest.mark.parametrize("name", [ACC + ".nc.bak", "prefix-" + ACC + ".nc", "../" + ACC + ".nc"])
def test_substring_and_path_matches_never_qualify(name: str) -> None:
    result = m.inspect_prefix([archive(name)], ACC)
    assert result["target_member"] is None
    assert not result["absence_inference_allowed"]


@pytest.mark.parametrize("accession", ["", "66740-22-000061", "../" + ACC, ACC + "\n"])
def test_invalid_accession(accession: str) -> None:
    with pytest.raises(ValueError, match="ACCESSION"):
        m.inspect_prefix([b"x"], accession)


def test_compressed_limit_before_decode() -> None:
    with pytest.raises(ValueError, match="COMPRESSED"):
        m.inspect_prefix([b"x" * 20], ACC, compressed_limit=10)


def test_compression_bomb_bound() -> None:
    raw = archive("other.nc", b"x" * 100_000)
    with pytest.raises(ValueError, match="DECODED"):
        m.inspect_prefix([raw], ACC, decoded_limit=2048)


def test_truncated_gzip_can_show_header_but_not_checksum() -> None:
    raw = archive(ACC + ".nc", b"x" * 100_000)
    result = m.inspect_prefix([raw[:-9]], ACC)
    assert result["target_member"] is not None
    assert not result["gzip_eof_and_checksum_available"]
    assert not result["public_time_certified"]


def test_invalid_gzip_is_rejected() -> None:
    with pytest.raises(ValueError, match="GZIP"):
        m.inspect_prefix([b"x" * 600], ACC)


def test_corrupt_gzip_checksum_is_rejected() -> None:
    raw = bytearray(archive(ACC + ".nc"))
    raw[-8] ^= 1
    with pytest.raises(ValueError, match="GZIP"):
        m.inspect_prefix([bytes(raw)], ACC)


def test_corrupt_initial_tar_checksum_is_rejected() -> None:
    raw = bytearray(gzip.decompress(archive(ACC + ".nc")))
    raw[0] ^= 1
    with pytest.raises(ValueError, match="TAR_INVALID"):
        m.inspect_prefix([gzip.compress(raw)], ACC)


def test_target_symlink_never_qualifies() -> None:
    with pytest.raises(ValueError, match="REGULAR"):
        m.inspect_prefix([archive(ACC + ".nc", kind=tarfile.SYMTYPE)], ACC)


def test_concatenated_streams_denied() -> None:
    raw = archive(ACC + ".nc")
    with pytest.raises(ValueError, match="CONCATENATED"):
        m.inspect_prefix([raw + raw], ACC)


def test_empty_input_denied() -> None:
    with pytest.raises(ValueError, match="INPUT"):
        m.inspect_prefix([], ACC)

