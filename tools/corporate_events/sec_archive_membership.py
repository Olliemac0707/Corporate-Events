"""Inspect cached SEC Feed prefixes without networking or certifying public time."""

from __future__ import annotations

import hashlib
import io
import re
import tarfile
import zlib
from typing import Any

MAX_COMPRESSED = 34_603_008
MAX_DECODED = 268_435_456


def inspect_prefix(
    chunks: list[bytes],
    accession: str,
    *,
    compressed_limit: int = MAX_COMPRESSED,
    decoded_limit: int = MAX_DECODED,
) -> dict[str, Any]:
    """Check bounded gzip/TAR data; exact headers establish only prefix membership."""
    if not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession):
        raise ValueError("ACCESSION_INVALID")
    if (
        not chunks
        or not 0 < compressed_limit <= MAX_COMPRESSED
        or not 0 < decoded_limit <= MAX_DECODED
        or any(not isinstance(chunk, bytes) or not chunk for chunk in chunks)
        or sum(map(len, chunks)) > compressed_limit
    ):
        raise ValueError("COMPRESSED_PREFIX_BUDGET_OR_INPUT_INVALID")
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    decoded = bytearray()
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk)
        try:
            decoded.extend(decoder.decompress(chunk, decoded_limit + 1 - len(decoded)))
        except zlib.error as error:
            raise ValueError("GZIP_INVALID") from error
        if len(decoded) > decoded_limit:
            raise ValueError("DECODED_PREFIX_BUDGET_EXCEEDED")
        if decoder.unused_data:
            raise ValueError("TRAILING_OR_CONCATENATED_GZIP_DENIED")
    if len(decoded) < 512:
        raise ValueError("TAR_HEADER_UNAVAILABLE")
    members = []
    target = None
    terminal = "PREFIX_END"
    try:
        with tarfile.open(fileobj=io.BytesIO(decoded), mode="r|") as stream:
            for member in stream:
                # tarfile verifies checksums; paths are never extracted or normalized.
                header_start = member.offset_data - 512
                header = bytes(decoded[header_start : header_start + 512])
                if len(header) != 512:
                    raise ValueError("TAR_HEADER_UNAVAILABLE")
                row = {
                    "name": member.name,
                    "size": member.size,
                    "header_byte_start": header_start,
                    "header_sha256": hashlib.sha256(header).hexdigest(),
                    "data_byte_start": member.offset_data,
                    "member_body_complete_in_prefix": (
                        member.offset_data + member.size <= len(decoded)
                    ),
                }
                members.append(row)
                if member.name in {accession + ".nc", "./" + accession + ".nc"}:
                    if not member.isfile():
                        raise ValueError("TARGET_NOT_REGULAR_FILE")
                    target = row
                    terminal = "TARGET_HEADER_FOUND"
                    break
    except (tarfile.ReadError, EOFError) as error:
        message = str(error)
        if "unexpected end of data" not in message and "empty file" not in message:
            raise ValueError("TAR_INVALID") from error
        terminal = "TRUNCATED_PREFIX"
    return {
        "status": "EXACT_TARGET_HEADER_LOCATED" if target else "TARGET_NOT_LOCATED_IN_PREFIX",
        "accession": accession,
        "compressed_prefix_sha256": digest.hexdigest(),
        "compressed_bytes": sum(map(len, chunks)),
        "decoded_bytes": len(decoded),
        "gzip_eof_and_checksum_available": decoder.eof,
        "scan_terminal": terminal,
        "member_headers_seen": len(members),
        "target_member": target,
        "member_headers": members,
        "absence_inference_allowed": False,
        "public_time_certified": False,
        "network_requests": 0,
        "filing_bodies_extracted": 0,
    }

