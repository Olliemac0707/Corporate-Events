"""Verify source-fact distinctions and evidence without market observations."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "bounded_ledger_v3_test", ROOT / "tools/corporate_events/bounded_repurchase_rules_v3.py"
)
assert spec and spec.loader
r: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = r
spec.loader.exec_module(r)


def doc(value: str, filename: str = "release.htm", form: str = "EX-99.1") -> Any:
    raw = value.encode()
    return r.p.base.Document(
        "0000000123-10-000001", filename, form, "EXHIBIT", "1", 100, 100 + len(raw), raw
    )


def scan(value: str) -> tuple[str, list[dict[str, Any]]]:
    source = doc(value)
    text = r.render(source)["text"]
    return text, r.scan(source, text, "8-K", "123")


def test_explicit_authorization_is_only_a_provisional_candidate() -> None:
    value = (
        "On May 12, 2010, the Board of Directors authorized a new program "
        "to repurchase up to $50 million of common stock in open-market transactions."
    )
    _, rows = scan("<p>" + value + "</p>")
    assert rows[0]["category"] == "CANDIDATE"
    assert rows[0]["facts"]["approval_date"] == "2010-05-12"
    assert rows[0]["facts"]["stated_announcement_date"] is None
    assert rows[0]["facts"]["public_disclosure_date"] is None
    assert rows[0]["facts"]["issuer_identity_certified"] is False
    assert rows[0]["certified_qualifying"] is False


def test_announced_date_is_not_silently_an_approval_date() -> None:
    _, rows = scan(
        "<p>On October 30, 2013, we announced that our Board of Directors had authorized "
        "the repurchase of up to an additional $50 million of our common stock.</p>"
    )
    assert rows[0]["facts"]["stated_announcement_date"] == "2013-10-30"
    assert rows[0]["facts"]["approval_date"] is None
    assert rows[0]["facts"]["public_disclosure_date"] is None


@pytest.mark.parametrize(
    "value,category,reason",
    [
        (
            "In the future, we may authorize additional repurchases of common stock.",
            "CONTEXT",
            "POSSIBLE_FUTURE",
        ),
        (
            "Our Board suspended the share repurchase authorization in May 2009.",
            "CONTEXT",
            "SUSPENSION",
        ),
        (
            "We repurchased 50,000 shares of common stock during the quarter.",
            "CONTEXT",
            "EXECUTED_PURCHASES",
        ),
        (
            "The Company launched a tender offer to repurchase common stock.",
            "REJECTION_CONTEXT",
            "TENDER_OR_ASR",
        ),
        (
            "The Company entered an accelerated share repurchase agreement.",
            "REJECTION_CONTEXT",
            "TENDER_OR_ASR",
        ),
        ("The Company will repurchase preferred shares.", "REJECTION_CONTEXT", "NON_COMMON"),
        (
            "We repurchased notes issued under the debt agreement.",
            "REJECTION_CONTEXT",
            "NON_COMMON",
        ),
        (
            "Shares were repurchased for tax withholding under employee benefit plans.",
            "REJECTION_CONTEXT",
            "EMPLOYEE",
        ),
        (
            "There remains $5 million under the existing share repurchase program.",
            "CONTEXT",
            "EXISTING_PROGRAM",
        ),
        ("The Company announced a common stock repurchase program.", "UNRESOLVED", "FULL_CONTEXT"),
    ],
)
def test_contexts_do_not_become_qualifying_events(value: str, category: str, reason: str) -> None:
    result = r.disposition(value, "8-K", "123")
    assert result["category"] == category
    assert reason in result["reason"]
    assert result["certified_qualifying"] is False


def test_actual_purchase_after_approval_remains_evidence_with_a_conflict_flag() -> None:
    result = r.disposition(
        "The Board authorized a $50 million common stock repurchase program "
        "and we repurchased 10,000 shares.",
        "8-K",
        "123",
    )
    assert result["category"] == "CANDIDATE"
    assert "AUTHORIZATION_AND_EXECUTION_MIXED_IN_PASSAGE" in result["unresolved_requirements"]


def test_inline_tags_table_rows_and_footnotes_remain_visible() -> None:
    text, rows = scan(
        "<table><tr><td>The Board authorized a new</td><td>repur<b>chase</b> "
        "of $50 million of common stock.</td></tr></table>"
        "<p>Footnote (1): purchases may occur in the open market.</p>"
    )
    assert "repurchase" in text and "Footnote (1)" in text and "$50 million" in text
    assert rows[0]["category"] == "CANDIDATE"


def test_scripts_and_hidden_nodes_are_inert() -> None:
    text, rows = scan(
        '<script>Board authorized share repurchase.</script><p style="display:none">'
        "Board authorized repurchase.</p><p>Visible information.</p>"
    )
    assert "authorized" not in text and rows == []


def test_optional_html_end_tags_do_not_lose_a_long_document() -> None:
    rendered = r.render(
        doc(
            "<html><body>"
            + "<p>ordinary paragraph" * 1000
            + "<p>The Board authorized a share repurchase program.</body></html>"
        )
    )
    assert rendered["status"] == "VISIBLE_TEXT_EXTRACTED" and "repurchase" in rendered["text"]


@pytest.mark.parametrize(
    "filename,form", [("image.gif", "GRAPHIC"), ("release.pdf", "EX-99"), ("sheet.xlsx", "EXCEL")]
)
def test_non_text_components_are_retained_with_an_explicit_limit(filename: str, form: str) -> None:
    result = r.render(doc("unparsed source", filename, form))
    assert result["status"] == "NON_TEXT_COMPONENT_RETAINED" and result["text"] == ""


def test_evidence_offsets_hash_and_source_bytes_are_traceable() -> None:
    source = doc("<p>Context.</p><p>The Board authorized a share repurchase program.</p>")
    text = r.render(source)["text"]
    row = r.scan(source, text, "8-K", "123")[0]
    proof = row["evidence"]
    assert text[proof["text_start"] : proof["text_end"]] == proof["quote"]
    assert r.p.base.digest(proof["quote"].encode()) == proof["quote_sha256"]
    assert proof["container_byte_start"] == 100 and proof["container_byte_end"] == 100 + len(
        source.raw
    )


def test_matching_dates_and_amounts_do_not_merge_separate_actions() -> None:
    rows = []
    for sentence in (
        "The Board authorized a new $50 million share repurchase program.",
        "The Company authorized an additional $50 million to repurchase common stock.",
    ):
        _, extracted = scan("<p>" + sentence + "</p>")
        extracted[0]["accession"] = "0000000123-10-000001"
        rows.extend(extracted)
    groups = r.proposed_groups(rows)
    assert len(groups) == 2 and all(x["economic_action_dedup_certified"] is False for x in groups)


def test_exact_repeated_wording_is_only_evidence_equivalence() -> None:
    sentence = "The Board authorized a $50 million share repurchase program."
    _, rows = scan("<p>" + sentence + "</p><p>" + sentence + "</p>")
    for row in rows:
        row["accession"] = "0000000123-10-000001"
    groups = r.proposed_groups(rows)
    assert len(groups) == 1 and len(groups[0]["members"]) == 2
    assert groups[0]["economic_action_dedup_certified"] is False


def test_no_keyword_result_never_establishes_a_negative_control() -> None:
    _, rows = scan("<p>The Company appointed a director.</p>")
    assert rows == []


def test_large_html_markup_is_processed_in_bounded_chunks() -> None:
    source = doc(
        "<html><!--" + "x" * (r.MAX_TEXT + 20) + "--><p>The Board authorized "
        "a common stock repurchase program.</p></html>"
    )
    result = r.render(source)
    assert result["status"] == "VISIBLE_TEXT_EXTRACTED"
    assert "repurchase" in result["text"] and len(result["text"]) < 200


def test_input_cap_remains_enforced(monkeypatch: Any) -> None:
    monkeypatch.setattr(r, "MAX_INPUT", 100)
    result = r.render(doc("x" * 101))
    assert result["status"] == "COMPONENT_INPUT_SIZE_LIMIT_RETAINED"
    assert result["text"] == ""


def test_html_source_wrapping_does_not_split_the_authorization_or_amount() -> None:
    value = (
        "<p>On May 12, 2010, the Board authorized a new share repurchase\n"
        "program of up to $50 million of common stock in open-market transactions.</p>"
    )
    text, rows = scan(value)
    assert len(rows) == 1 and "up to $50 million" in rows[0]["evidence"]["quote"]
    assert rows[0]["facts"]["authorization_amounts_as_disclosed"][0]["decimal"] == "50"
    assert rows[0]["facts"]["open_market_permission"] is True
    assert "repurchase program" in text


def test_plain_text_wrapping_keeps_one_complete_paragraph() -> None:
    value = (
        "The Company announced that its board of directors has approved a new\n"
        "stock repurchase program, authorizing up to $10 million of common stock.\n\n"
        "Separate background paragraph."
    )
    text, rows = scan(value)
    assert len(rows) == 1 and "$10 million" in rows[0]["evidence"]["quote"]
    assert "\nSeparate background" in text


@pytest.mark.parametrize("defect", ["quote", "hash", "offset", "source", "qualifying", "time"])
def test_replay_rejects_changed_evidence_or_premature_certification(defect: str) -> None:
    import copy

    spec = importlib.util.spec_from_file_location(
        "bounded_evidence_v3_test", ROOT / "tools/corporate_events/bounded_repurchase_ledger_v3.py"
    )
    assert spec and spec.loader
    ledger: Any = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = ledger
    spec.loader.exec_module(ledger)
    source = doc("<p>The Board authorized a share repurchase program.</p>")
    text = r.render(source)["text"]
    row = r.scan(source, text, "8-K", "123")[0]
    row.update(
        accession=source.accession,
        container_sha256="f" * 64,
        source_filing_date_is_public_time=False,
    )
    component = {
        "accession": source.accession,
        "container_sha256": "f" * 64,
        "raw_document_sha256": source.sha256,
        "container_byte_start": source.start,
        "container_byte_end": source.end,
    }
    ledger.verify_evidence(row, component, text)
    changed = copy.deepcopy(row)
    if defect == "quote":
        changed["evidence"]["quote"] = "different"
    elif defect == "hash":
        changed["evidence"]["quote_sha256"] = "0" * 64
    elif defect == "offset":
        changed["evidence"]["text_start"] = -1
    elif defect == "source":
        changed["container_sha256"] = "0" * 64
    elif defect == "qualifying":
        changed["certified_qualifying"] = True
    else:
        changed["source_filing_date_is_public_time"] = True
    with pytest.raises(ValueError, match="EVIDENCE_OR_WITHHOLDING"):
        ledger.verify_evidence(changed, component, text)
