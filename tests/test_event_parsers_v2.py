"""Regression cases derived from the initial review; no market data or network."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "cea_v2_regression", ROOT / "tools/corporate_events/event_parsers_v2.py"
)
assert spec and spec.loader
p: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = p
spec.loader.exec_module(p)
ACC = "0000000123-24-000001"


def doc(text: str, form: str = "8-K", filename: str = "body.htm", kind: str = "BODY") -> Any:
    raw = text.encode()
    return p.base.Document(ACC, filename, form, kind, "1", 0, len(raw), raw)


def parse(text: str, form: str = "8-K", **kwargs: Any) -> dict[str, Any]:
    source = doc(
        text,
        kwargs.pop("raw_form", form),
        kwargs.pop("filename", "body.htm"),
        kwargs.pop("kind", "BODY"),
    )
    return dict(p.narrative(source, form, "123", **kwargs))


def family(result: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [r for r in result["observations"] if r["family"] == name]


@pytest.mark.parametrize(
    "text",
    [
        (
            "<p>X</p><p>- Definition</p><p>Total common shares inclu"
            "de issued and repurchased shares.</p>"
        ),
        "<p>Namespace Prefix: us-gaap_</p><p>Share repurchase program authorized amount.</p>",
        '{"documentation":"Number of shares repurchased during the period."}',
    ],
)
def test_taxonomy_text_never_creates_issuer_actions(text: str) -> None:
    result = parse(text)
    assert not result["observations"]
    assert result["coverage_status"] == "UNRESOLVED_NOT_A_WHOLE_SOURCE_NEGATIVE"
    assert result["retained_contexts"]


@pytest.mark.parametrize(
    "text",
    [
        (
            "If the Company makes a spin-off or repurchase of shares"
            ", the Committee shall adjust awards."
        ),
        "In the event of a stock dividend, spin-off or repurchase, award limits shall be adjusted.",
        "The separate existence of Merger Subsidiary shall cease following the merger.",
        "The sales organization is separated into business units.",
        "An executive formerly led the Thermo Separation Products subsidiary.",
    ],
)
def test_contract_and_non_event_meanings_are_retained_without_events(text: str) -> None:
    result = parse(text, "S-4")
    assert not family(result, "SPINOFF_ANNOUNCEMENT")
    assert result["retained_contexts"]


def test_no_expiration_is_not_an_expiration_event() -> None:
    result = parse(
        "The Company has a $200 million common stock repurchase program. "
        "The stock repurchase program has no expiration date. "
        "The Company repurchased 52561 common shares during 2024.",
        "10-Q",
        kind="PERIODIC",
    )
    rows = family(result, "SHARE_REPURCHASE")
    assert rows and all(r["proposed_state"] != "EXPIRATION" for r in rows)
    assert any(r["proposed_state"] == "ROUTINE_ACTUAL_REPURCHASES" for r in rows)


def test_capacity_separated_from_execution_and_no_subtraction() -> None:
    result = parse(
        "On August 1, 2021, our Board of Directors authorized a "
        "$100 million common stock repurchase program. The Compa"
        "ny repurchased common shares totaling $15.8 million in "
        "fiscal 2023."
    )
    rows = family(result, "SHARE_REPURCHASE")
    approval = next(r for r in rows if r["proposed_state"] == "NEW_AUTHORIZATION")
    actual = next(r for r in rows if r["proposed_state"] == "ROUTINE_ACTUAL_REPURCHASES")
    assert approval["facts"]["authorization_amounts_as_disclosed"][0]["decimal"] == "100"
    assert approval["facts"]["approval_date"] == "2021-08-01"
    assert actual["facts"]["authorization_amounts_as_disclosed"] == []
    assert all(r["facts"]["incremental_amount_inferred"] is False for r in rows)


def test_renewal_does_not_certify_incremental_capacity() -> None:
    result = parse(
        "On May 7, 2021, the Company's Board approved renewal of"
        " its common stock repurchase program for up to $500 mil"
        "lion."
    )
    row = family(result, "SHARE_REPURCHASE")[0]
    assert row["proposed_state"] == "AMBIGUOUS"
    assert row["facts"]["renewal_requires_capacity_review"]
    assert row["facts"]["incremental_amount_as_disclosed"] is None


@pytest.mark.parametrize(
    "text",
    [
        "We record repurchases of common stock as treasury stock at cost.",
        "Debt covenants could limit our ability to repurchase common stock.",
        "Repurchases of common stock are accounted for at cost including brokerage fees.",
    ],
)
def test_accounting_and_restrictions_are_not_authorizations(text: str) -> None:
    assert not family(parse(text), "SHARE_REPURCHASE")


def test_repeated_dated_approval_consolidates_but_retains_both_citations() -> None:
    sentence = (
        "On March 3, 2016, the Board approved a new $15 million common stock repurchase program."
    )
    rows = family(parse(sentence + " " + sentence), "SHARE_REPURCHASE")
    assert len(rows) == 1 and len(rows[0]["additional_evidence"]) == 1
    assert rows[0]["certified_qualifying"] is False


def test_distinct_approval_dates_are_not_merged() -> None:
    result = parse(
        "On March 3, 2016, the Board approved a new $15 million common stock repurchase program. "
        "On March 4, 2016, the Board approved a new $15 million common stock repurchase program."
    )
    assert len(family(result, "SHARE_REPURCHASE")) == 2


@pytest.mark.parametrize(
    "text",
    [
        "We have never declared or paid a cash dividend.",
        "The Board approved dividend equivalents for employee restricted stock units.",
        "Interest and dividend income are excluded from the investment reconciliation.",
        "The Preferred Stock dividend rate will increase by 1 percent per annum.",
        "The Company declared a $1.25 dividend on preferred stock.",
    ],
)
def test_non_common_and_negated_dividends_do_not_become_common_actions(text: str) -> None:
    assert not family(parse(text), "DIVIDEND_POSITIVE_CHANGE")


def test_dividend_keeps_prefix_dates_class_and_adjacent_payment_sentence() -> None:
    result = parse(
        "On February 5, 2024, the Company's Board declared a qua"
        "rterly cash dividend of $0.55 per share of common stock"
        ". The dividend is payable on March 4, 2024, to sharehol"
        "ders of record at the close of business on February 20,"
        " 2024."
    )
    rows = family(result, "DIVIDEND_POSITIVE_CHANGE")
    assert len(rows) == 1
    row = rows[0]
    assert row["facts"]["declaration_date"] == "2024-02-05"
    assert row["facts"]["payment_date"] == "2024-03-04"
    assert row["facts"]["record_date"] == "2024-02-20"
    assert row["facts"]["sec_class_label"] == "COMMON"
    assert row["facts"]["declared_cash_per_share_decimal"] == "0.55"
    assert row["facts"]["currency"] is None
    assert row["proposed_state"] == "UNKNOWN_BASELINE"
    e = row["evidence"]
    excerpt = result["text"][e["text_start"] : e["text_end"]]
    assert excerpt.startswith("On February 5") and "February 20" in excerpt


def test_fragmented_html_does_not_drop_negation() -> None:
    result = parse("<p>We have never declared or paid a <b>cash dividend</b>.</p>")
    assert not family(result, "DIVIDEND_POSITIVE_CHANGE")
    assert "never declared or paid a cash dividend" in result["text"]


def test_unrelated_increase_is_not_a_dividend_increase() -> None:
    result = parse(
        "The Company declared a quarterly cash dividend of $0.55 per share of common stock. "
        "Our investment income increased by ten percent."
    )
    row = family(result, "DIVIDEND_POSITIVE_CHANGE")[0]
    assert row["facts"]["source_reports_increase"] is False


def test_item4_evidence_uses_purpose_not_cover_header() -> None:
    text = "COVER PAGE\nItem 4 Purpose of Transaction .... 5\nItem 5 .... 6\n" + (
        "Header only. " * 90
    )
    text += (
        "\nItem 4. Purpose of Transaction\nThe Reporting Person in"
        "tends to nominate two directors to the board of the Iss"
        "uer.\nItem 5. Interest in Securities."
    )
    result = parse(
        text,
        "INITIAL_13D",
        raw_form="SC 13D",
        roles={"subject_cik_raw": "123", "filer_cik_raw": "456"},
    )
    row = family(result, "ACTIVIST_13D")[0]
    assert row["proposed_state"] == "ACTIVIST_INTENT_SUPPORTED"
    proof = row["evidence"]
    assert "intends to nominate" in result["text"][proof["text_start"] : proof["text_end"]]
    assert row["facts"]["subject_issuer_cik"] == "0000000123"
    assert row["facts"]["reporting_filer_cik"] == "0000000456"
    assert row["facts"]["overlapping_holdings_summed"] is False


@pytest.mark.parametrize(
    "filename,raw_form,text",
    [
        ("joint.htm", "EX-99.1", "Joint Filing Agreement. The parties agree to file jointly."),
        ("schedule.htm", "EX-99.2", "Transaction schedule. Shares purchased on dates shown."),
    ],
)
def test_supporting_13d_exhibits_are_not_item4_events(
    filename: str, raw_form: str, text: str
) -> None:
    result = parse(text, "INITIAL_13D", raw_form=raw_form, filename=filename, kind="EXHIBIT")
    assert not family(result, "ACTIVIST_13D")
    assert result["retained_contexts"][0]["reason"] == "SUPPORTING_COMPONENT_NOT_INDEPENDENT_ITEM4"


@pytest.mark.parametrize(
    "text,state",
    [
        (
            (
                "The Reporting Person has the right to designate all per"
                "sons to serve as directors on the board of the Issuer."
            ),
            "ACTIVIST_INTENT_SUPPORTED",
        ),
        (
            (
                "The shares were acquired for investment purposes only a"
                "nd the Reporting Person has no present plans or proposa"
                "ls."
            ),
            "NONACTIVIST_INITIAL_13D",
        ),
        (
            (
                "The shares were acquired for investment purposes only. "
                "The Reporting Person may communicate with management an"
                "d the board regarding proposals."
            ),
            "INTENT_UNRESOLVED",
        ),
    ],
)
def test_specific_control_and_generic_reserved_rights_stay_distinct(text: str, state: str) -> None:
    result = parse(
        "Item 4. Purpose of Transaction\n" + text + "\nItem 5. Interests.",
        "INITIAL_13D",
        raw_form="SC 13D",
    )
    assert family(result, "ACTIVIST_13D")[0]["proposed_state"] == state


def test_no_item4_does_not_substitute_header_evidence() -> None:
    result = parse(
        "Cover page and signatures. No purpose section found.", "INITIAL_13D", raw_form="SC 13D"
    )
    assert not result["observations"]
    assert result["retained_contexts"][0]["reason"] == "ITEM4_SCOPE_UNRESOLVED_NO_HEADER_SUBSTITUTE"


def test_spin_parent_is_not_assumed_from_filer() -> None:
    result = parse(
        (
            "On July 26, 2022, the Parent announced its plan to sepa"
            "rate its health care business into an independent publi"
            "c company."
        ),
        "10-12B",
        kind="REGISTRATION",
    )
    row = family(result, "SPINOFF_ANNOUNCEMENT")[0]
    assert row["proposed_state"] == "SPINOFF_INTENTION"
    assert row["facts"]["filer_cik_from_index"] == "123"
    assert row["facts"]["parent_cik_from_index"] is None
    assert row["facts"]["recaps_prior_announcement"]
    assert row["facts"]["first_disclosure_status"] == "LEFT_CENSORED_HISTORY"
    assert row["facts"]["registration_is_first_announcement"] is False


def test_historical_split_off_is_recap_without_parent_assignment() -> None:
    result = parse(
        (
            "On December 22, 2003, DIRECTV split-off from GM and no "
            "longer filed a consolidated tax return."
        ),
        "S-4",
    )
    row = family(result, "SPINOFF_ANNOUNCEMENT")[0]
    assert row["proposed_state"] == "COMPLETION_ONLY_RECAP"
    assert row["facts"]["parent_cik_from_index"] is None


def test_strategic_alternatives_are_not_a_completed_asset_sale() -> None:
    result = parse(
        (
            "The Board reviewed strategic alternatives including a s"
            "ale or spin-off of portfolios and a possible merger."
        ),
        "S-4",
    )
    row = family(result, "SPINOFF_ANNOUNCEMENT")[0]
    assert row["proposed_state"] == "STRATEGIC_ALTERNATIVES_REVIEW"


@pytest.mark.parametrize(
    "filename,text",
    [
        ("taxonomy.json", '{"documentation":"Board authorized a $100 million stock repurchase."}'),
        ("instance.xml", "<xbrl><text>Company declared a cash dividend.</text></xbrl>"),
        ("scan.pdf", "%PDF-1.4 unreviewed bytes"),
    ],
)
def test_structured_and_unsupported_sources_are_retained_not_negative(
    filename: str, text: str
) -> None:
    result = parse(text, filename=filename)
    assert not result["observations"]
    assert (
        result["retained_contexts"][0]["reason"]
        == "NON_NARRATIVE_OR_EMBEDDED_COMPONENT_RETAINED_UNRESOLVED"
    )


def test_hidden_and_context_data_are_not_narrative() -> None:
    text, _ = p.render(
        b"<script>Company approved stock repurchase.</script><ix:"
        b"hidden>hidden</ix:hidden><xbrli:context>taxonomy</xbrli"
        b":context><p>Visible.</p>"
    )
    assert text == "Visible."


def test_all_narrative_evidence_is_exactly_bound_to_original_component() -> None:
    source = doc(
        "On March 3, 2016, the Board approved a new $15 million common stock repurchase program."
    )
    result = p.narrative(source, "8-K", "123")
    for row in result["observations"]:
        proof = row["evidence"]
        assert proof["source_document_sha256"] == source.sha256
        assert proof["extractor_version"] == p.VERSION
        assert proof["derived_text_sha256"] == p.base.digest(result["text"].encode())
        assert proof["evidence_sha256"] == p.base.digest(
            result["text"][proof["text_start"] : proof["text_end"]].encode()
        )
        assert row["public_time_status"] == "TIMESTAMP_UNRESOLVED"


def test_documentary_links_are_only_suggestions_with_same_accession() -> None:
    text = "On March 3, 2016, the Board approved a new $15 million common stock repurchase program."
    row = family(parse(text), "SHARE_REPURCHASE")[0]
    left = {**row, "accession": ACC, "development_observation_id": "left"}
    right = {**row, "accession": ACC, "development_observation_id": "right"}
    links = p.documentary_links([left, right])
    assert len(links) == 1 and not links[0]["final_dedup_certified"]
    assert not p.documentary_links([left, {**right, "accession": "0000000123-24-000002"}])


def test_missing_action_date_prevents_cross_document_link() -> None:
    row = family(
        parse("The Board approved a new $15 million common stock repurchase program."),
        "SHARE_REPURCHASE",
    )[0]
    assert not p.documentary_links(
        [
            {**row, "accession": ACC, "development_observation_id": "one"},
            {**row, "accession": ACC, "development_observation_id": "two"},
        ]
    )


def test_sgml_xbrl_wrapper_around_inline_html_is_narrative() -> None:
    text = (
        "<XBRL><?xml version='1.0'?><!--Created by filing software-->"
        "<html><body><ix:header><ix:hidden>Hidden metadata</ix:hidden>"
        "<xbrli:context>Taxonomy data</xbrli:context></ix:header><p>"
        "On February 5, 2024, the Board declared a quarterly cash dividend of "
        "$0.55 per share of common stock.</p></body></html></XBRL>"
    )
    result = parse(text)
    assert len(family(result, "DIVIDEND_POSITIVE_CHANGE")) == 1
    assert "Hidden metadata" not in result["text"]
    assert "Taxonomy data" not in result["text"]


def test_item4_cross_references_do_not_create_purpose_sections() -> None:
    text = (
        "Item 4. Purpose of Transaction\n"
        "The Reporting Person may communicate with management about matters "
        "described in Item 4 of Schedule 13D.\n"
        "Except as described in the exhibits attached, there is no present plan.\n"
        "Item 4 of Schedule 13D is referenced again for context.\n"
        "Item 5. Interests in Securities."
    )
    row = family(parse(text, "INITIAL_13D", raw_form="SC 13D"), "ACTIVIST_13D")[0]
    assert row["facts"]["item4_sections_found"] == 1
    assert row["facts"]["other_section_or_exhibit_corroboration_required"]
    assert row["facts"]["reserved_rights_language"]


@pytest.mark.parametrize(
    "text,state",
    [
        (
            "The Company entered an accelerated share repurchase agreement for common stock.",
            "ASR_AGREEMENT",
        ),
        ("The Company announced a tender offer to repurchase common shares.", "TENDER_OFFER"),
        ("The Company extended its common stock repurchase program.", "EXTENSION_ONLY"),
        ("The common stock repurchase program has expired.", "EXPIRATION"),
        ("The common stock repurchase program was completed.", "COMPLETION"),
    ],
)
def test_supported_repurchase_contexts_are_not_lost(text: str, state: str) -> None:
    rows = family(parse(text), "SHARE_REPURCHASE")
    assert len(rows) == 1 and rows[0]["proposed_state"] == state


def test_approval_and_execution_amount_in_same_sentence_do_not_mix() -> None:
    text = (
        "The Board authorized a new $100 million common stock repurchase program "
        "and the Company repurchased common shares for $15.8 million."
    )
    row = family(parse(text), "SHARE_REPURCHASE")[0]
    assert [v["decimal"] for v in row["facts"]["authorization_amounts_as_disclosed"]] == ["100"]


def test_previously_authorized_existing_program_is_not_new_capacity() -> None:
    row = family(
        parse("The Board previously authorized an existing program to repurchase common stock."),
        "SHARE_REPURCHASE",
    )[0]
    assert row["proposed_state"] == "AMBIGUOUS"


@pytest.mark.parametrize(
    "text",
    [
        "We will become independent after completion of the planned spin-off from Parent.",
        "The completion of the spin-off is subject to effectiveness of the registration statement.",
        "The spin-off will be completed following regulatory approval.",
        "The Company has not completed the planned spin-off.",
        "Upon completion of the spin-off, the executive will become our chief executive.",
    ],
)
def test_future_or_conditional_completion_is_not_a_completed_event(text: str) -> None:
    rows = family(parse(text, "10-12B"), "SPINOFF_ANNOUNCEMENT")
    assert all(row["proposed_state"] != "COMPLETION_ONLY_RECAP" for row in rows)
    assert all(row["facts"]["future_completion_used"] is False for row in rows)


@pytest.mark.parametrize(
    "text",
    [
        "The Company completed its spin-off of the subsidiary on January 1, 2020.",
        "The spin-off was completed on January 1, 2020.",
    ],
)
def test_explicit_past_completion_remains_a_recap(text: str) -> None:
    row = family(parse(text, "S-4"), "SPINOFF_ANNOUNCEMENT")[0]
    assert row["proposed_state"] == "COMPLETION_ONLY_RECAP"
    assert row["facts"]["completion_status"] == "STATED_COMPLETED"


def test_possible_future_cancellation_is_not_a_cancelled_proposal() -> None:
    rows = family(
        parse(
            "The planned spin-off may be cancelled if regulatory approval is not obtained.",
            "10-12B",
        ),
        "SPINOFF_ANNOUNCEMENT",
    )
    assert all(row["proposed_state"] != "CANCELLED_PROPOSAL" for row in rows)


def test_company_abbreviation_does_not_cut_off_approval_date() -> None:
    result = parse(
        "On March 3, 2016, the Board of Directors of Example, Inc. "
        "(the Company) approved a new $15 million common stock repurchase program."
    )
    row = family(result, "SHARE_REPURCHASE")[0]
    assert row["facts"]["approval_date"] == "2016-03-03"
    e = row["evidence"]
    assert "On March 3, 2016" in result["text"][e["text_start"] : e["text_end"]]


def test_standing_program_that_authorizes_purchases_is_not_new_approval() -> None:
    result = parse(
        "The Company has a common stock repurchase program that authorizes up to "
        "$200 million of purchases. The program has no expiration date."
    )
    assert not family(result, "SHARE_REPURCHASE")
    assert result["retained_contexts"]


@pytest.mark.parametrize(
    "text",
    [
        "After the spin-off has been completed, we will be independent.",
        "Once the Company has completed its spin-off, the shares will be distributed.",
    ],
)
def test_conditional_perfect_tense_does_not_certify_completion(text: str) -> None:
    rows = family(parse(text, "10-12B"), "SPINOFF_ANNOUNCEMENT")
    assert all(row["proposed_state"] != "COMPLETION_ONLY_RECAP" for row in rows)
