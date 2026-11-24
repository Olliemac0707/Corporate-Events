"""Structural and semantic regressions from v2 documentary QA; no outcomes."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "cea_v3_test", ROOT / "tools/corporate_events/event_parsers_v3.py"
)
assert spec and spec.loader
p: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = p
spec.loader.exec_module(p)
ACC = "0000000123-24-000001"


def source(text: str, form: str = "8-K", kind: str = "BODY") -> Any:
    raw = text.encode()
    return p.base.Document(ACC, "body.htm", form, kind, "1", 0, len(raw), raw)


def parse(text: str, canonical: str = "8-K", form: str | None = None) -> dict[str, Any]:
    return dict(p.narrative(source(text, form or canonical), canonical, "123"))


def rows(result: dict[str, Any], family: str) -> list[dict[str, Any]]:
    return [x for x in result["observations"] if x["family"] == family]


def test_dom_hierarchy_and_sentence_roles_are_preserved() -> None:
    result = parse(
        "<html><body><div><p>The Board approved a new $15 millio"
        "n common stock repurchase program.</p></div></body></ht"
        "ml>"
    )
    assert len(rows(result, "SHARE_REPURCHASE")) == 1
    assert result["structural_blocks"]
    assert rows(result, "SHARE_REPURCHASE")[0]["structural_context"]["dom_node"] > 0


def test_whole_definition_subtree_including_second_sentence_is_excluded() -> None:
    result = parse(
        '<table id="defref_us-gaap_CommonStock"><tr><td><p>'
        "Value of shares outstanding. Excludes common shares repurchased and held in treasury."
        "</p></td></tr></table>"
    )
    assert not result["observations"]
    assert any(x["reason"] == "TAXONOMY_DEFINITION_SUBTREE" for x in result["retained_contexts"])


def test_table_numbers_never_become_narrative_capacity() -> None:
    text = (
        "<table><tr><th>Average price</th><th>Remaining amount</th></tr>"
        "<tr><td>$22</td><td>$42000000</td></tr>"
        '<tr><td colspan="2"><p>On May 7, 2021, the Board approved renewal of the '
        "share repurchase program for up to $500 million.</p></td></tr></table>"
    )
    row = rows(parse(text), "SHARE_REPURCHASE")[0]
    assert row["proposed_state"] == "AMBIGUOUS"
    assert [x["decimal"] for x in row["facts"]["authorization_amounts_as_disclosed"]] == ["500"]
    assert row["facts"]["incremental_amount_as_disclosed"] is None


def test_performance_section_does_not_spill_into_next_event_section() -> None:
    text = (
        "<p><b>Stock Price Performance Graph</b></p><p>The graph"
        " assumes $100.</p><table><tr><td>$81</td><td>$199</td><"
        "/tr></table><p><b>Share Repurchases</b></p><p>In August"
        " 2021, our Board of Directors authorized a $100 million"
        " share repurchase program.</p>"
    )
    result = parse(text, "10-K")
    row = rows(result, "SHARE_REPURCHASE")[0]
    assert [x["decimal"] for x in row["facts"]["authorization_amounts_as_disclosed"]] == ["100"]
    assert "graph assumes" not in result["text"]
    assert "$199" not in result["text"]


@pytest.mark.parametrize(
    "text",
    [
        "Our Board suspended the buyback authorization and has not set a reinitiation date.",
        "In the future, we may authorize additional common stock repurchases.",
        (
            "Unless terminated by the Board, the program will expire"
            " when we have repurchased the shares authorized."
        ),
        (
            "We purchased common shares, which represented the full "
            "amount authorized under the repurchase program."
        ),
        "All shares repurchased were retired.",
    ],
)
def test_nouns_future_conditions_and_retirement_are_not_new_approvals(text: str) -> None:
    result = parse("<p>" + text + "</p>", "10-K")
    assert all(
        row["proposed_state"] != "NEW_AUTHORIZATION" for row in rows(result, "SHARE_REPURCHASE")
    )


def test_actuals_remain_actuals_despite_authorized_amount_reference() -> None:
    result = parse(
        (
            "<p>We purchased 2000 common shares, which represented t"
            "he full amount authorized under the repurchase program."
            "</p>"
        ),
        "10-K",
    )
    row = rows(result, "SHARE_REPURCHASE")[0]
    assert row["proposed_state"] == "ROUTINE_ACTUAL_REPURCHASES"
    assert row["facts"]["authorization_amounts_as_disclosed"] == []


def test_announcement_and_approval_dates_are_separate() -> None:
    result = parse(
        (
            "<p>On October 30, 2013, we announced that our Board had"
            " authorized an additional $50 million of common stock r"
            "epurchases.</p>"
        ),
        "10-K",
    )
    facts = rows(result, "SHARE_REPURCHASE")[0]["facts"]
    assert facts["stated_announcement_date"] == "2013-10-30"
    assert facts["approval_date"] is None
    assert facts["capacity_and_action_identity_require_review"]


def test_direct_approval_date_remains_available() -> None:
    row = rows(
        parse(
            "<p>On March 3, 2016, the Board approved a new $15 milli"
            "on common stock repurchase program.</p>"
        ),
        "SHARE_REPURCHASE",
    )[0]
    assert row["facts"]["approval_date"] == "2016-03-03"
    assert row["facts"]["stated_announcement_date"] is None


def test_per_share_ceiling_is_not_total_authorization() -> None:
    text = (
        "<p>In September 2002, the Board authorized repurchases "
        "of up to 500,000 shares of common stock at a price no g"
        "reater than $1.25 per share.</p>"
    )
    facts = rows(parse(text, "10-Q"), "SHARE_REPURCHASE")[0]["facts"]
    assert facts["authorization_amounts_as_disclosed"] == []
    assert facts["authorization_share_limit_as_disclosed"] == "500000"
    assert facts["reported_month"] == "September 2002"


@pytest.mark.parametrize(
    "text",
    [
        (
            "Although we do not currently expect to pay dividends, r"
            "estrictions could affect any dividends if declared in f"
            "uture."
        ),
        "The Company's common book value increased after considering accumulated dividends paid.",
        "Interest and dividend income increased during the quarter.",
    ],
)
def test_dividend_requires_declaration_actor_verb_object(text: str) -> None:
    assert not rows(parse("<p>" + text + "</p>"), "DIVIDEND_POSITIVE_CHANGE")


def test_common_declaration_fields_and_date() -> None:
    result = parse(
        "<p>On February 5, 2024, the Board declared a quarterly "
        "cash dividend of $0.55 per common share, payable on Mar"
        "ch 4, 2024, to shareholders of record on February 20, 2"
        "024.</p>"
    )
    row = rows(result, "DIVIDEND_POSITIVE_CHANGE")[0]
    assert row["facts"]["sec_class_label"] == "COMMON"
    assert row["facts"]["declaration_date"] == "2024-02-05"
    assert row["facts"]["record_date"] == "2024-02-20"
    assert row["facts"]["payment_date"] == "2024-03-04"
    assert row["proposed_state"] == "UNKNOWN_BASELINE"


def test_two_dividends_do_not_silently_get_one_record_date() -> None:
    result = parse(
        "<p>The Board declared dividends of $0.36 per common sha"
        "re to shareholders of record on March 15, 2021 and June"
        " 15, 2021, and the Company paid the dividends on March "
        "31, 2021 and June 30, 2021.</p>"
    )
    facts = rows(result, "DIVIDEND_POSITIVE_CHANGE")[0]["facts"]
    assert facts["record_date"] is None
    assert facts["record_dates_as_disclosed"] == ["2021-03-15", "2021-06-15"]
    assert facts["payment_dates_as_disclosed"] == ["2021-03-31", "2021-06-30"]
    assert facts["declaration_identity_status"] == "MULTIPLE_DISTRIBUTIONS_REVIEW"


def test_information_statement_is_not_contract_because_agreement_is_mentioned() -> None:
    result = parse(
        (
            "<p>Information Statement</p><p>Pursuant to a separation"
            " agreement, WPC intends to spin off its business into a"
            " separate company.</p>"
        ),
        "10-12B",
        "EX-99.1",
    )
    row = rows(result, "SPINOFF_ANNOUNCEMENT")[0]
    assert row["facts"]["source_component_role"] == "INFORMATION_STATEMENT"
    assert row["facts"]["parent_cik_from_index"] is None


def test_hyphenated_pro_rata_is_recognized() -> None:
    row = rows(
        parse(
            (
                "<p>The separation will be effected by a pro-rata distri"
                "bution to shareholders through a planned spin-off.</p>"
            ),
            "10-12B",
        ),
        "SPINOFF_ANNOUNCEMENT",
    )[0]
    assert row["facts"]["pro_rata_distribution_stated"]


@pytest.mark.parametrize(
    "text",
    [
        (
            "The Board approved a recommendation to pursue discussio"
            "ns regarding the possibility of a spin-off transaction."
        ),
        "A representative proposed a possible sponsored spin transaction to the Board.",
    ],
)
def test_discussion_and_third_party_proposals_do_not_become_committed_intent(text: str) -> None:
    row = rows(parse("<p>" + text + "</p>", "S-4"), "SPINOFF_ANNOUNCEMENT")[0]
    assert row["proposed_state"] == "STRATEGIC_ALTERNATIVES_REVIEW"


def test_heading_before_if_does_not_defeat_conditional_scope() -> None:
    text = (
        "<p>Corporate Transactions</p><p>If stock changes becaus"
        "e of a stock dividend, spin-off or distribution, awards"
        " under the ESPP will be adjusted.</p>"
    )
    assert not rows(parse(text, "10-12B"), "SPINOFF_ANNOUNCEMENT")


def test_identical_words_at_different_locations_are_not_merged() -> None:
    doc = source("All shares repurchased were retired. All shares repurchased were retired.")
    text = doc.raw.decode()
    first = p.new_observation(
        "SHARE_REPURCHASE",
        "ROUTINE_ACTUAL_REPURCHASES",
        {},
        p.evidence(doc, text, 0, 36),
        {},
        "PERIODIC_FILING",
    )
    second = p.new_observation(
        "SHARE_REPURCHASE",
        "ROUTINE_ACTUAL_REPURCHASES",
        {},
        p.evidence(doc, text, 37, len(text)),
        {},
        "PERIODIC_FILING",
    )
    assert len(p.consolidate([first, second])) == 2


def test_periodic_parent_context_survives_note_document_kind() -> None:
    row = rows(
        parse(
            "<p>On May 26, 2011, the Board authorized a $50 million common stock repurchase.</p>",
            "10-K",
            "XML",
        ),
        "SHARE_REPURCHASE",
    )[0]
    assert row["facts"]["parent_periodic_filing"]
    assert row["facts"]["chronology_requires_review"]


def test_all_proposals_remain_uncertified_and_evidence_matches() -> None:
    doc = source("<p>The Board approved a new $15 million common stock repurchase program.</p>")
    result = p.narrative(doc, "8-K", "123")
    for row in result["observations"]:
        e = row["evidence"]
        assert e["source_document_sha256"] == doc.sha256
        assert e["evidence_sha256"] == p.base.digest(
            result["text"][e["text_start"] : e["text_end"]].encode()
        )
        assert not row["certified_qualifying"]
        assert row["public_time_status"] == "TIMESTAMP_UNRESOLVED"


def test_optional_paragraph_end_tags_do_not_create_unbounded_depth() -> None:
    text = "<html><body>" + "<p>Ordinary narrative." * 600
    text += "<p>The Board approved a new $15 million common stock repurchase program.</body></html>"
    result = parse(text)
    assert rows(result, "SHARE_REPURCHASE")
    assert not any(
        x["reason"] == "STRUCTURAL_LIMIT_RETAIN_SOURCE" for x in result["retained_contexts"]
    )


def test_periodic_genre_precedes_incidental_information_statement_reference() -> None:
    result = parse(
        "<p>Information Statement incorporated by reference.</p>"
        "<p>The Board approved a new $15 million common stock repurchase program.</p>",
        "10-K",
    )
    assert result["source_genre"] == "PERIODIC_FILING"


def test_plain_text_item4_keeps_heading_boundaries_and_negative_flag() -> None:
    text = (
        "COVER\n\nItem 4. Purpose of Transaction\n"
        "No Reporting Person has any present plan or proposal concerning control. "
        "The Reporting Person may communicate with management.\n\nItem 5. Interests."
    )
    result = parse(text, "INITIAL_13D", "SC 13D")
    row = rows(result, "ACTIVIST_13D")[0]
    assert row["facts"]["investment_or_no_present_plan_language"]
    assert row["proposed_state"] == "INTENT_UNRESOLVED"


def test_initials_in_legal_name_are_not_separate_sentences() -> None:
    result = parse(
        "<p>Information Statement</p>"
        "<p>W. P. Carey Inc. intends to spin off its business into a separate company.</p>",
        "10-12B",
        "EX-99.1",
    )
    row = rows(result, "SPINOFF_ANNOUNCEMENT")[0]
    e = row["evidence"]
    assert result["text"][e["text_start"] : e["text_end"]].startswith("W. P. Carey")


def test_dividend_adjacent_payment_sentence_keeps_association() -> None:
    result = parse(
        "<p>On February 5, 2024, the Board declared a quarterly cash dividend "
        "of $0.55 per common share. The dividend is payable on March 4, 2024, "
        "to shareholders of record on February 20, 2024.</p>"
    )
    row = rows(result, "DIVIDEND_POSITIVE_CHANGE")[0]
    assert row["facts"]["record_date"] == "2024-02-20"
    assert row["facts"]["payment_date"] == "2024-03-04"


def test_numbered_heading_is_not_a_standalone_spin_candidate() -> None:
    result = parse(
        "<p>A. Proposed Spin-off.</p><p>The Board approved the spin-off of its business.</p>",
        "10-12B",
    )
    observations = rows(result, "SPINOFF_ANNOUNCEMENT")
    assert len(observations) == 1
    e = observations[0]["evidence"]
    assert "Board approved" in result["text"][e["text_start"] : e["text_end"]]


def test_contents_link_does_not_turn_later_business_into_compensation_section() -> None:
    text = (
        '<table><tr><td><a href="#comp">Executive Compensation</a></td></tr></table>'
        "<p>The Board approved a plan to separate Alpha & Beta banking business "
        "and the technology business into two publicly-traded companies.</p>"
    )
    result = parse(text, "10-12B")
    assert rows(result, "SPINOFF_ANNOUNCEMENT")
    assert any(x["reason"] == "NAVIGATION_ENTRY_RETAINED" for x in result["retained_contexts"])


def test_contract_title_is_not_overridden_by_defined_information_statement_term() -> None:
    text = (
        "<p>SEPARATION AND DISTRIBUTION AGREEMENT</p>"
        "<p>This Agreement defines the Information Statement.</p>"
        "<p>WHEREAS, the Board determined to separate the SpinCo business "
        "and create a publicly-traded company.</p>"
    )
    result = parse(text, "10-12B", "EX-2.1")
    assert result["source_genre"] == "SUPPORTING_CONTRACT"


def test_conditional_subheading_does_not_permanently_hide_later_operative_paragraph() -> None:
    text = (
        "<p>Corporate Transactions</p><p>If stock changes, employee awards will be adjusted.</p>"
        "<p>The Board approved a new $15 million common stock repurchase program.</p>"
    )
    assert rows(parse(text), "SHARE_REPURCHASE")


def test_plural_record_dates_do_not_hide_later_actual_payment_dates() -> None:
    result = parse(
        "<p>The Board of Directors of Alpha declared dividends of $0.36 per common share, "
        "payable to common shareholders of record on March 15, 2021 and June 15, 2021, "
        "and the Company paid the dividends on March 31, 2021 and June 30, 2021, respectively.</p>"
    )
    facts = rows(result, "DIVIDEND_POSITIVE_CHANGE")[0]["facts"]
    assert facts["record_dates_as_disclosed"] == ["2021-03-15", "2021-06-15"]
    assert facts["reported_paid_dates_as_disclosed"] == ["2021-03-31", "2021-06-30"]
    assert facts["scheduled_payment_dates_as_disclosed"] == []
    assert facts["payment_dates_as_disclosed"] == ["2021-03-31", "2021-06-30"]
    assert facts["record_date"] is None and facts["payment_date"] is None
    assert not facts["single_action_certified"]


def test_scheduled_and_reported_actual_payment_dates_keep_distinct_roles() -> None:
    result = parse(
        "<p>The Board declared a cash dividend of $0.50 per common share payable on July 1, 2024, "
        "to shareholders of record on June 10, 2024, and the Company paid it on July 2, 2024.</p>"
    )
    facts = rows(result, "DIVIDEND_POSITIVE_CHANGE")[0]["facts"]
    assert facts["scheduled_payment_dates_as_disclosed"] == ["2024-07-01"]
    assert facts["reported_paid_dates_as_disclosed"] == ["2024-07-02"]
    assert facts["payment_date_role_status"] == "MULTIPLE_PAYMENT_DATE_ROLES_REVIEW"
    assert facts["payment_date"] is None
    assert facts["record_date"] is None


def test_not_paid_does_not_become_a_reported_payment_date() -> None:
    result = parse(
        "<p>The Board declared a cash dividend of $0.50 per common share payable on July 1, 2024, "
        "to shareholders of record on June 10, 2024, and the Company has not paid it "
        "as of July 2, 2024.</p>"
    )
    facts = rows(result, "DIVIDEND_POSITIVE_CHANGE")[0]["facts"]
    assert facts["reported_paid_dates_as_disclosed"] == []
    assert facts["scheduled_payment_dates_as_disclosed"] == ["2024-07-01"]
