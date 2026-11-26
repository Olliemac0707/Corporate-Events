"""Constrain source-bound label normalization and preserve uncertainty."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "listing_replay_test", ROOT / "tools/corporate_events/sec_feed_listing_replay.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
URL = m.directory.URLS[0]


def body(directory_name: str, **item_values: Any) -> bytes:
    return json.dumps({"directory": {"name": directory_name, "parent-dir": "../", "item": [
        {"name": "20070802.nc.tar.gz", "href": "20070802.nc.tar.gz", **item_values},
    ]}}).encode()


@pytest.mark.parametrize("name", [
    "Feed/2007/QTR3/", "/Archives/edgar/Feed/2007/QTR3",
    "/Archives/edgar/Feed/2007/QTR3/",
])
def test_exact_source_bound_variants(name: str) -> None:
    raw = body(name)
    result = m.decode(raw, URL)
    assert result["listing_items"] == 1
    assert result["source_body_sha256"] == m.sec.sha(raw)
    assert result["new_HTTP_requests"] == result["public_times_certified"] == 0
    assert result["automatic_followup_requests"] == 0
    assert result["accession_membership_proven"] is False
    assert result["directory_dates_used_as_public_time"] is False
    assert result["other_quarters_inferred"] is False
    assert result["observed_directory_name"] == name


@pytest.mark.parametrize("name", [
    "Feed/2008/QTR3/", "Feed/2007/QTR2/", "../Feed/2007/QTR3/",
    "feed/2007/QTR3/", "https://www.sec.gov/Archives/edgar/Feed/2007/QTR3/",
    "/Archives/edgar/Feed/2007/QTR3//", "Feed/2007/QTR3/%2e%2e",
])
def test_foreign_or_ambiguous_directory_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="IDENTITY"):
        m.decode(body(name), URL)


def test_duplicate_keys_rejected_before_normalization() -> None:
    with pytest.raises(ValueError, match="DUPLICATE"):
        m.decode(b'{"directory":{},"directory":{}}', URL)


@pytest.mark.parametrize("href", ["../other", "https://example.com/file", "other.nc.tar.gz"])
def test_href_cannot_redirect_name_identity(href: str) -> None:
    with pytest.raises(ValueError, match="HREF"):
        m.decode(body("Feed/2007/QTR3/", href=href), URL)


@pytest.mark.parametrize("name", ["../evil", "a/b", "a\\b"])
def test_original_name_safety_survives_normalization(name: str) -> None:
    with pytest.raises(ValueError, match="UNSAFE"):
        m.decode(body("Feed/2007/QTR3/", name=name, href=name), URL)


def test_parent_hint_and_unregistered_url_rejected() -> None:
    data = json.loads(body("Feed/2007/QTR3/"))
    data["directory"]["parent-dir"] = "https://example.com/"
    with pytest.raises(ValueError, match="PARENT"):
        m.decode(json.dumps(data).encode(), URL)
    with pytest.raises(ValueError, match="SCOPE"):
        m.decode(body("Feed/2007/QTR3/"), "https://example.com/index.json")
