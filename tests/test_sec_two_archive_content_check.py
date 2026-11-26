"""Verify component comparison preserves order and refuses to hide content differences."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "content_check_test", ROOT / "tools/corporate_events/sec_two_archive_content_check.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def wrap(*docs: bytes, newline: bytes = b"\n") -> bytes:
    return b"".join(
        b"<DOCUMENT>" + newline + doc + newline + b"</DOCUMENT>" + newline for doc in docs
    )


def test_line_endings_can_match_but_real_content_differences_are_retained() -> None:
    result = m.compare(wrap(b"first", b"second", newline=b"\r\n"), wrap(b"first", b"second"))
    assert result["all_documents_match_after_crlf_normalization"]
    assert not result["nonmatching_components_require_review"]
    result = m.compare(wrap(b"first", b"s -econd"), wrap(b"first", b"second"))
    assert not result["all_documents_match_after_crlf_normalization"]
    assert result["nonmatching_components_require_review"]
    assert result["pairs"][1]["first_difference_after_linebreak_removal"] == 1


def test_linebreak_removal_is_reported_separately() -> None:
    result = m.compare(wrap(b"x\ny"), wrap(b"xy"))
    assert not result["all_documents_match_after_crlf_normalization"]
    assert result["pairs"][0]["match_after_removing_all_line_breaks"]
    assert result["nonmatching_components_require_review"]


def test_missing_or_reordered_components_do_not_match() -> None:
    assert m.compare(wrap(b"one", b"two"), wrap(b"one"))["nonmatching_components_require_review"]
    assert m.compare(wrap(b"one", b"two"), wrap(b"two", b"one"))[
        "nonmatching_components_require_review"
    ]


@pytest.mark.parametrize("raw", [b"", b"<DOCUMENT>\nmissing close", b"</DOCUMENT>\n"])
def test_malformed_document_boundaries_are_rejected(raw: bytes) -> None:
    with pytest.raises(ValueError, match="BOUNDARIES"):
        m.documents(raw)
