"""Compare target archive document components with the previously cached SEC filings offline."""

from __future__ import annotations

import json
import re
import sys
import tarfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sec_timing_diagnostics as base  # noqa: E402
import sec_two_archive_ranges as core  # noqa: E402

ROOT, PRIVATE, PROTOCOL = core.ROOT, core.PRIVATE, core.PROTOCOL
OUTPUT = PRIVATE / "two-archive-content-check-v1"
REGISTERED = {
    "tools/corporate_events/sec_two_archive_content_check.py",
    "tests/test_sec_two_archive_content_check.py",
    "protocols/corporate-event-alpha-v1/sec-two-archive-content-check-policy-v1.json",
}


def documents(raw: bytes) -> list[bytes]:
    starts = list(re.finditer(rb"(?m)^<DOCUMENT>[ \t]*\r?$", raw))
    ends = list(re.finditer(rb"(?m)^</DOCUMENT>[ \t]*\r?$", raw))
    if not starts or len(starts) != len(ends):
        raise ValueError("CONTENT_DOCUMENT_BOUNDARIES_INVALID")
    result = []
    for index, (start, end) in enumerate(zip(starts, ends, strict=True)):
        if end.start() < start.end() or (
            index + 1 < len(starts) and end.end() > starts[index + 1].start()
        ):
            raise ValueError("CONTENT_DOCUMENT_ORDER_INVALID")
        block = raw[start.end() : end.start()].replace(b"\r\n", b"\n")
        if block.startswith(b"\n"):
            block = block[1:]
        if block.endswith(b"\n"):
            block = block[:-1]
        result.append(block)
    return result


def compare(source: bytes, archived: bytes) -> dict[str, Any]:
    left, right = documents(source), documents(archived)
    pairs = []
    for index, (a, b) in enumerate(zip(left, right, strict=False)):
        flat_a, flat_b = a.replace(b"\n", b""), b.replace(b"\n", b"")
        first_difference = next(
            (i for i, (x, y) in enumerate(zip(flat_a, flat_b, strict=False)) if x != y),
            min(len(flat_a), len(flat_b)) if len(flat_a) != len(flat_b) else None,
        )
        pairs.append(
            {
                "ordinal": index + 1,
                "source_lf_normalized_sha256": core.sec.sha(a),
                "archive_lf_normalized_sha256": core.sec.sha(b),
                "match_after_crlf_normalization": a == b,
                "match_after_removing_all_line_breaks": flat_a == flat_b,
                "first_difference_after_linebreak_removal": first_difference,
            }
        )
    return {
        "source_document_count": len(left),
        "archive_document_count": len(right),
        "all_documents_match_after_crlf_normalization": len(left) == len(right)
        and all(p["match_after_crlf_normalization"] for p in pairs),
        "pairs": pairs,
        "nonmatching_components_require_review": len(left) != len(right)
        or any(not p["match_after_crlf_normalization"] for p in pairs),
    }


def run() -> dict[str, Any]:
    plan, _ = core.preflight()
    registry = json.loads(
        (PROTOCOL / "sec-two-archive-content-check-extension-v1.json").read_bytes()
    )
    if set(registry["files"]) != REGISTERED:
        raise ValueError("CONTENT_REGISTRY_SCOPE_INVALID")
    for name, digest in registry["files"].items():
        if core.sec.file_sha(ROOT / name) != digest:
            raise ValueError("CONTENT_REGISTERED_FILE_CHANGED")
    policy = json.loads((PROTOCOL / "sec-two-archive-content-check-policy-v1.json").read_bytes())
    for name, digest in policy["private_inputs"].items():
        if core.sha_file(PRIVATE / name) != digest:
            raise ValueError("CONTENT_PINNED_INPUT_CHANGED")
    core.sec.EXTENSION_FILES = (
        *core.sec.EXTENSION_FILES,
        *core.REGISTERED,
        *REGISTERED,
        *plan["policy"]["dependencies"],
    )
    core.sec.install_firewall()
    sys.addaudithook(
        lambda event, args: (
            (_ for _ in ()).throw(RuntimeError("CONTENT_CHECK_NETWORK_DENIED"))
            if event.startswith(("socket.", "http.client."))
            else None
        )
    )
    inspection = json.loads((PRIVATE / "two-archive-inspection-v1/summary.json").read_bytes())
    rows = []
    for target, proof in zip(plan["targets"], inspection["proofs"], strict=True):
        if not proof["gzip_eof_and_checksum_verified"] or len(proof["matches"]) != 1:
            raise ValueError("CONTENT_TARGET_MEMBERSHIP_NOT_VERIFIED")
        archive = PRIVATE / "two-archive-inspection-v1/archives" / (proof["archive_sha256"] + ".gz")
        if core.sha_file(archive) != proof["archive_sha256"]:
            raise ValueError("CONTENT_ARCHIVE_HASH_CHANGED")
        source = core.sec.Cache(PRIVATE / "repurchase-acquisition-v1").read(target["source_sha256"])
        body = None
        with tarfile.open(archive, mode="r|gz") as tar:
            for member in tar:
                if member.name == proof["matches"][0]["member_name"]:
                    stream = tar.extractfile(member)
                    if stream is None or member.size > core.TARGET_CAP:
                        raise ValueError("CONTENT_TARGET_MEMBER_UNREADABLE")
                    body = stream.read(core.TARGET_CAP + 1)
                    break
        if body is None or core.sec.sha(body) != proof["matches"][0]["member_sha256"]:
            raise ValueError("CONTENT_TARGET_MEMBER_HASH_CHANGED")
        rows.append({"accession": target["accession"], **compare(source, body)})
    summary = {
        "status": "OFFLINE_COMPONENT_COMPARISON_COMPLETE",
        "results": rows,
        "new_HTTP_requests": 0,
        "public_times_certified": 0,
        "original_membership_record_modified": False,
        "independent_full_source_reviews": 0,
        "firewall_counters": dict.fromkeys(core.sec.FIREWALL_COUNTERS, 0),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    base.immutable(OUTPUT / "summary.json", summary)
    base.immutable(
        OUTPUT / "receipt.json",
        {
            "summary_sha256": core.sec.file_sha(OUTPUT / "summary.json"),
            "registry": registry,
            "private_inputs": policy["private_inputs"],
        },
    )
    return summary


if __name__ == "__main__":
    try:
        print(json.dumps(run()), flush=True)
    except Exception as error:
        print(json.dumps({"status": "CONTENT_CHECK_STOPPED", "reason": base.safe_reason(error)}))
        raise SystemExit(2) from None
