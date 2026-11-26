"""Offline-only, provenance-preserving replay of exact SEC Feed directory labels."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sec_feed_directory as directory  # noqa: E402
import sec_timing_diagnostics as base  # noqa: E402

sec = directory.sec
ROOT, PRIVATE, PROTOCOL = directory.ROOT, directory.PRIVATE, directory.PROTOCOL
OUTPUT = PRIVATE / "repurchase-feed-listing-replay-v1"
REGISTERED = {
    "tools/corporate_events/sec_feed_listing_replay.py",
    "tests/test_sec_feed_listing_replay.py",
    "protocols/corporate-event-alpha-v1/sec-feed-listing-replay-policy-v1.json",
}


def decode(body: bytes, url: str) -> dict[str, Any]:
    if url not in directory.URLS or len(body) > directory.CAP:
        raise ValueError("LISTING_SOURCE_OUTSIDE_SCOPE")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("LISTING_DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    data = json.loads(body.decode("utf-8"), object_pairs_hook=unique)
    if not isinstance(data, dict) or not isinstance(data.get("directory"), dict):
        raise ValueError("LISTING_SCHEMA_INVALID")
    metadata = data["directory"]
    expected = urlsplit(url).path.removesuffix("/index.json")
    relative = expected.removeprefix("/Archives/edgar/") + "/"
    observed = metadata.get("name")
    if observed not in {expected, expected + "/", relative}:
        raise ValueError("LISTING_DIRECTORY_IDENTITY_MISMATCH")
    if metadata.get("parent-dir", "../") != "../":
        raise ValueError("LISTING_PARENT_HINT_UNEXPECTED")
    items = metadata.get("item")
    if not isinstance(items, list) or len(items) > 4096:
        raise ValueError("LISTING_ITEM_LIST_INVALID")
    for item in items:
        if not isinstance(item, dict) or (
            "href" in item and item["href"] != item.get("name")
        ):
            raise ValueError("LISTING_ITEM_HREF_MISMATCH")
    metadata["name"] = expected
    normalized = base.canonical(data)
    result = directory.parse_listing(normalized, url)
    result.update({
        "source_body_sha256": sec.sha(body),
        "normalized_derivative_sha256": sec.sha(normalized),
        "observed_directory_name": observed,
        "normalization": "Exact source-URL-bound full or Feed-relative directory label only.",
        "new_HTTP_requests": 0,
        "other_quarters_inferred": False,
        "listed_names_are_exhaustive_SEC_availability_claim": False,
    })
    return result


def preflight() -> dict[str, Any]:
    directory.preflight()
    registry = json.loads((PROTOCOL / "sec-feed-listing-replay-extension-v1.json").read_bytes())
    if set(registry["files"]) != REGISTERED:
        raise ValueError("LISTING_REGISTRY_SCOPE_MISMATCH")
    for name, digest in registry["files"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("LISTING_REGISTERED_FILE_CHANGED")
    policy: dict[str, Any] = json.loads(
        (PROTOCOL / "sec-feed-listing-replay-policy-v1.json").read_bytes()
    )
    for name, digest in policy["private_inputs"].items():
        if sec.file_sha(PRIVATE / name) != digest:
            raise ValueError("LISTING_PINNED_SOURCE_CHANGED")
    policy["registry"] = registry
    return policy


def run() -> dict[str, Any]:
    policy = preflight()
    sec.EXTENSION_FILES = (
        *sec.EXTENSION_FILES, *directory.REGISTERED,
        *json.loads((PROTOCOL / "sec-feed-directory-policy-v1.json").read_bytes())[
            "frozen_dependencies"
        ], *REGISTERED,
    )
    sec.install_firewall()
    sys.addaudithook(lambda event, args: (
        (_ for _ in ()).throw(RuntimeError("LISTING_REPLAY_NETWORK_DENIED"))
        if event.startswith(("socket.", "http.client.")) else None
    ))
    source = PRIVATE / "repurchase-feed-directory-v1"
    state = json.loads((source / "state.json").read_bytes())
    if state["status"] != "STOPPED" or state["stop_reason"] != "DIRECTORY_PATH_MISMATCH":
        raise ValueError("LISTING_EXPECTED_PRESERVED_STOP_RECORD")
    results = []
    for attempt in state["attempts"]:
        if attempt["http_status"] != 200 or attempt["outcome"] != "DIRECTORY_PATH_MISMATCH":
            raise ValueError("LISTING_SOURCE_NOT_A_COMPLETE_METADATA_RESPONSE")
        raw = sec.Cache(source).read(attempt["body_sha256"])
        length = directory.checked_headers(200, list(attempt["response_headers"].items()))
        if len(raw) != length or len(raw) != attempt["received_body_bytes"]:
            raise ValueError("LISTING_CACHED_RESPONSE_LENGTH_MISMATCH")
        results.append(decode(raw, attempt["url"]))
    if len(results) != 1:
        raise ValueError("LISTING_EXPECTED_EXACT_ONE_CACHED_RESPONSE")
    summary = {
        "version": "sec-feed-listing-replay-v1", "status": "OFFLINE_LISTING_VALIDATED",
        "listings": results, "listing_items": sum(r["listing_items"] for r in results),
        "source_HTTP_attempts": len(state["attempts"]),
        "source_received_body_bytes": state["received_body_bytes"],
        "new_HTTP_requests": 0, "original_stop_record_modified": False,
        "unused_quarter_requests_automatically_resumed": False,
        "public_times_certified": 0, "regressions_run": 0,
        "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    base.immutable(OUTPUT / "summary.json", summary)
    base.immutable(OUTPUT / "replay-receipt.json", {
        "summary_sha256": sec.file_sha(OUTPUT / "summary.json"),
        "registry": policy["registry"], "private_inputs": policy["private_inputs"],
    })
    directory.verify_prior_attempts(json.loads(
        (PROTOCOL / "sec-feed-directory-policy-v1.json").read_bytes()
    ))
    return summary


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        result = run()
        print(json.dumps({k: v for k, v in result.items() if k != "listings"}))
    except Exception as error:
        print(json.dumps({"status": "LISTING_REPLAY_STOPPED", "reason": base.safe_reason(error)}))
        raise SystemExit(2) from None
