"""Scan a bounded gzip prefix for exact SEC archive members without certifying time."""

from __future__ import annotations

import gzip
import hashlib
import io
import re
import tarfile
import zlib
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Any

MAX_COMPRESSED = 210_763_776
MAX_DECODED = 8_589_934_592
MAX_TARGET = 33_554_432


class Joined(io.RawIOBase):
    """Read immutable source fragments sequentially without concatenating them in memory."""

    def __init__(self, streams: list[Any]) -> None:
        super().__init__()
        self.streams = streams
        self.index = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        while self.index < len(self.streams):
            count = self.streams[self.index].readinto(buffer)
            if count:
                return int(count)
            self.index += 1
        return 0


def scan(
    paths: list[Path],
    expected: dict[str, bytes],
    compare: Callable[[bytes, bytes], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Verify complete target headers/bodies within a prefix; a miss proves no absence."""
    if (
        not paths
        or not expected
        or len(paths) > 5
        or any(not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", key) for key in expected)
    ):
        raise ValueError("PARTIAL_SCAN_SCOPE_INVALID")
    if any(not path.is_file() for path in paths):
        raise ValueError("PARTIAL_SCAN_FRAGMENT_MISSING")
    total = sum(path.stat().st_size for path in paths)
    if not 0 < total <= MAX_COMPRESSED:
        raise ValueError("PARTIAL_SCAN_COMPRESSED_LIMIT")
    digests = []
    for path in paths:
        digest = hashlib.sha256()
        with path.open("rb") as fragment:
            while block := fragment.read(1_048_576):
                digest.update(block)
        digests.append({"bytes": path.stat().st_size, "sha256": digest.hexdigest()})
    found: dict[str, dict[str, Any]] = {}
    header_count, decoded_count = 0, 0
    outcome = "PREFIX_EXHAUSTED"
    with ExitStack() as stack:
        streams = [stack.enter_context(path.open("rb")) for path in paths]
        compressed = io.BufferedReader(Joined(streams), buffer_size=65_536)
        with gzip.GzipFile(fileobj=compressed, mode="rb") as stream:

            def read(count: int) -> bytes:
                nonlocal decoded_count
                raw = stream.read(count)
                decoded_count += len(raw)
                if decoded_count > MAX_DECODED:
                    raise ValueError("PARTIAL_SCAN_DECODED_LIMIT")
                return raw

            try:
                while True:
                    offset = decoded_count
                    header = read(512)
                    if len(header) != 512:
                        break
                    if header == bytes(512):
                        outcome = "TAR_END_REACHED_GZIP_CHECKSUM_NOT_ASSERTED"
                        break
                    try:
                        member = tarfile.TarInfo.frombuf(header, "utf-8", "surrogateescape")
                    except tarfile.HeaderError:
                        raise ValueError("PARTIAL_SCAN_TAR_HEADER_INVALID") from None
                    if member.type in {
                        tarfile.XHDTYPE,
                        tarfile.XGLTYPE,
                        tarfile.GNUTYPE_LONGNAME,
                        tarfile.GNUTYPE_LONGLINK,
                        tarfile.GNUTYPE_SPARSE,
                    }:
                        raise ValueError("PARTIAL_SCAN_EXTENDED_TAR_UNSUPPORTED")
                    header_count += 1
                    accession = next(
                        (a for a in expected if member.name in {a + ".nc", "./" + a + ".nc"}), None
                    )
                    if member.size < 0 or (
                        accession
                        and (accession in found or not member.isfile() or member.size > MAX_TARGET)
                    ):
                        raise ValueError("PARTIAL_SCAN_TARGET_TYPE_SIZE_OR_DUPLICATE_INVALID")
                    remain = ((member.size + 511) // 512) * 512
                    offset_in_member = 0
                    body = bytearray()
                    while remain:
                        piece = read(min(1_048_576, remain))
                        if not piece:
                            raise EOFError("truncated tar member")
                        if accession and offset_in_member < member.size:
                            body.extend(piece[: member.size - offset_in_member])
                        offset_in_member += len(piece)
                        remain -= len(piece)
                    if accession:
                        raw = bytes(body)
                        header_text = raw[:65_536].split(b"<DOCUMENT>", 1)[0]
                        ids = re.findall(
                            rb"(?im)^(?:<ACCESSION-NUMBER>\s*|ACCESSION NUMBER:\s*)"
                            rb"([0-9]{10}-[0-9]{2}-[0-9]{6})\s*$",
                            header_text,
                        )
                        if not ids or set(ids) != {accession.encode()}:
                            raise ValueError("PARTIAL_SCAN_INTERNAL_ACCESSION_MISMATCH")
                        found[accession] = {
                            "accession": accession,
                            "member_name": member.name,
                            "header_decoded_offset": offset,
                            "tar_header_sha256": hashlib.sha256(header).hexdigest(),
                            "tar_header_checksum_verified": True,
                            "member_body_bytes": len(raw),
                            "member_body_sha256": hashlib.sha256(raw).hexdigest(),
                            "internal_accession_verified": True,
                            "source_bytes_identical": raw == expected[accession],
                            "source_sha256": hashlib.sha256(expected[accession]).hexdigest(),
                        }
                        if compare is not None:
                            found[accession]["component_comparison"] = compare(
                                expected[accession], raw
                            )
                        if set(found) == set(expected):
                            outcome = "ALL_TARGET_BODIES_LOCATED_IN_PREFIX"
                            break
            except EOFError:
                outcome = "PREFIX_EXHAUSTED"
            except (gzip.BadGzipFile, OSError, zlib.error) as error:
                raise ValueError("PARTIAL_SCAN_GZIP_INVALID") from error
    return {
        "status": outcome,
        "compressed_bytes": total,
        "fragment_hashes": digests,
        "decoded_bytes": decoded_count,
        "member_headers_checked": header_count,
        "matches": [found[k] for k in sorted(found)],
        "unlocated_accessions": sorted(set(expected) - set(found)),
        "gzip_eof_and_checksum_verified": False,
        "absence_inference_allowed": False,
        "public_times_certified": 0,
        "unrelated_filing_bodies_extracted": 0,
    }
