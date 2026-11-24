"""Source-context regressions for the separately registered v4 development parser."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "cea_v4_fact_test", ROOT / "tools/corporate_events/event_parsers_v4.py"
)
assert spec and spec.loader
p: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = p
spec.loader.exec_module(p)


def parse(text: str, canonical: str = "8-K", form: str | None = None) -> dict[str, Any]:
    raw = text.encode()
    doc = p.base.Document(
        "0000000123-24-000001", "body.htm", form or canonical, "BODY", "", 0, len(raw), raw
    )
    return dict(p.narrative(doc, canonical, "123"))


def rows(result: dict[str, Any], family: str) -> list[dict[str, Any]]:
    return [x for x in result["observations"] if x["family"] == family]


@pytest.mark.parametrize(
    "heading",
    [
        "A. Proposed Spin-off.",
        "A. Proposed Spin‑off.",
        "Proposed Spinoff of Global Nonwovens and Hygiene Films Business",
        "Proposed Spin-off",
        "1. Proposed Spin-off.",
    ],
)
def test_heading_is_not_an_action_and_adjacent_approval_survives(heading: str) -> None:
    result = parse(
        "<p>" + heading + "</p><p>The Board approved the spin-off of its business "
        "as a separate publicly traded company.</p>",
        "10-12B",
    )
    found = rows(result, "SPINOFF_ANNOUNCEMENT")
    assert len(found) == 1
    e = found[0]["evidence"]
    assert "Board approved" in result["text"][e["text_start"] : e["text_end"]]


def test_inline_numbered_heading_keeps_actual_following_sentence() -> None:
    result = parse(
        "<p>A. Proposed Spin-off. On August 1, 2006, the Board approved the spin-off "
        "of its brokerage business to stockholders as a public company.</p>",
        "10-12B",
    )
    found = rows(result, "SPINOFF_ANNOUNCEMENT")
    assert len(found) == 1
    assert not result["text"][found[0]["evidence"]["text_start"] :].startswith("A.")


@pytest.mark.parametrize(
    "value",
    [
        "For more information on how the material terms of the spin-off were determined, "
        "see The Separation and the Distribution.",
        "See The Spin-off and Reasons for the Separation.",
    ],
)
def test_cross_reference_does_not_supply_operative_intention(value: str) -> None:
    assert not rows(parse("<p>" + value + "</p>", "10-12B"), "SPINOFF_ANNOUNCEMENT")


@pytest.mark.parametrize(
    "value",
    [
        "The computation of funds from operations may be adjusted by directors. "
        "Funds from operations will be determined from the date of the spin-off.",
        "Funds from operations will be determined from the date of the spin-off "
        "without regard to prior performance.",
        "The HHNF Business that Berry has agreed to spinoff generated net sales of $20 million.",
    ],
)
def test_financial_definition_and_relative_description_are_context(value: str) -> None:
    result = parse("<p>" + value + "</p>", "10-12B")
    assert not rows(result, "SPINOFF_ANNOUNCEMENT")
    assert any(x["reason"].startswith("FINANCIAL_") for x in result["retained_contexts"])


def test_operative_agreement_is_not_removed_because_it_mentions_sales() -> None:
    result = parse(
        "<p>The Company announced that it entered definitive agreements to spin off "
        "its business, which generated sales of $20 million.</p>",
        "S-4",
    )
    assert rows(result, "SPINOFF_ANNOUNCEMENT")


@pytest.mark.parametrize(
    "form,expected",
    [
        ("DEFM14A", "MERGER_PROXY_STATEMENT"),
        ("PREM14A", "MERGER_PROXY_STATEMENT"),
        ("DEF 14A", "PROXY_STATEMENT"),
    ],
)
def test_proxy_form_precedes_shareholder_salutation(form: str, expected: str) -> None:
    result = parse(
        "<p>SCHEDULE 14A</p><p>Dear Shareholder</p>"
        "<p>The board reviewed strategic alternatives including a spin-off.</p>",
        form,
    )
    assert result["source_genre"] == expected


@pytest.mark.parametrize("form", ["10-12B", "DEFM14A"])
def test_genuine_combined_proxy_prospectus_information_statement(form: str) -> None:
    result = parse(
        "<p>PROXY STATEMENT/PROSPECTUS-INFORMATION STATEMENT</p>"
        "<p>The Board approved the spin-off of its banking business.</p>",
        form,
    )
    assert result["source_genre"] == "PROXY_PROSPECTUS_INFORMATION_STATEMENT"


def test_information_statement_remains_distinct_from_contract_reference() -> None:
    result = parse(
        "<p>Information Statement</p><p>Pursuant to a separation agreement, "
        "WPC intends to spin off its business.</p>",
        "10-12B",
        "EX-99.1",
    )
    assert result["source_genre"] == "INFORMATION_STATEMENT"


def test_explicit_adjoining_total_capacity_is_retained_with_separate_proof() -> None:
    result = parse(
        "<p>In September 2002, our Board authorized the repurchase of up to "
        "500,000 shares of common stock at a share price no greater than $1.25. "
        "There is no requirement that we repurchase all or any portion of such shares. "
        "The maximum total value of the repurchase is not to exceed $500,000.</p>",
        "10-Q",
    )
    row = rows(result, "SHARE_REPURCHASE")[0]
    assert row["facts"]["authorization_share_limit_as_disclosed"] == "500000"
    assert [a["decimal"] for a in row["facts"]["authorization_amounts_as_disclosed"]] == ["500000"]
    assert row["facts"]["action_year_eligibility"] == "DISCLOSED_YEAR_PRE_STUDY_CONTEXT"
    assert len(row["additional_evidence"]) == 1
    extra = row["additional_evidence"][0]
    assert "maximum total value" in result["text"][extra["text_start"] : extra["text_end"]]
    assert row["facts"]["supporting_field_evidence"]["authorization_amounts_as_disclosed"] == [
        extra["evidence_id"]
    ]


def test_same_program_common_class_is_attached_without_guessing_expiry() -> None:
    result = parse(
        "<p>On March 3, 2016, the Board approved a $15 million stock repurchase program. "
        "Under the new Repurchase Program, the Company may repurchase up to "
        "$15 million of its common stock. The Repurchase Program may continue "
        "for up to eighteen months.</p>"
    )
    row = rows(result, "SHARE_REPURCHASE")[0]
    assert row["facts"]["sec_class_label"] == "COMMON"
    assert not row["facts"]["adjacent_program_fact_conflict"]
    assert row["facts"]["expiration_date"] is None
    assert len(row["additional_evidence"]) == 1


@pytest.mark.parametrize(
    "tail",
    [
        "</p><p>Under the new Repurchase Program, the Company may repurchase "
        "$15 million of common stock.",
        " Under the other program, the Company may repurchase $15 million of common stock.",
        " Under the new Repurchase Program, the Company may repurchase preferred stock.",
        " The Board authorized a different common stock program.",
    ],
)
def test_unlinked_or_conflicting_adjacent_class_is_not_transferred(tail: str) -> None:
    result = parse("<p>The Board approved a $15 million stock repurchase program." + tail + "</p>")
    row = rows(result, "SHARE_REPURCHASE")[0]
    assert row["facts"]["sec_class_label"] is None
    assert not row["additional_evidence"]


def test_conflicting_capacity_is_flagged_without_replacing_primary_amount() -> None:
    result = parse(
        "<p>The Board approved a $15 million stock repurchase program. "
        "Under the new Repurchase Program, the Company may repurchase "
        "up to $20 million of its common stock.</p>"
    )
    row = rows(result, "SHARE_REPURCHASE")[0]
    assert row["facts"]["adjacent_program_fact_conflict"]
    assert row["facts"]["authorization_amounts_as_disclosed"][0]["decimal"] == "15"
    assert row["facts"]["supporting_program_facts"][0]["amounts"][0]["decimal"] == "20"


def test_printed_dateline_is_not_board_approval_or_verified_public_time() -> None:
    result = parse(
        "<p>FORT LAUDERDALE, Fla., March 3, 2016—Alpha Inc. today announced "
        "that its Board approved a $15 million common stock repurchase program.</p>"
    )
    row = rows(result, "SHARE_REPURCHASE")[0]
    assert row["facts"]["stated_announcement_date"] == "2016-03-03"
    assert row["facts"]["stated_announcement_date_basis"] == "PRINTED_DATELINE_WITH_TODAY_ANNOUNCED"
    assert row["facts"]["approval_date"] is None
    assert row["facts"]["public_disclosure_date"] is None
    assert row["public_time_status"] == "TIMESTAMP_UNRESOLVED"


def test_dateline_without_announcement_wording_remains_unresolved() -> None:
    result = parse(
        "<p>NEW YORK, N.Y., March 3, 2016—The Board approved a "
        "$15 million common stock repurchase program.</p>"
    )
    assert rows(result, "SHARE_REPURCHASE")[0]["facts"]["stated_announcement_date"] is None


def test_another_program_relationship_is_not_certified_as_new() -> None:
    result = parse(
        "<p>On October 30, 2013, we announced that our Board authorized "
        "another repurchase of up to $50 million of our common stock.</p>",
        "10-K",
    )
    row = rows(result, "SHARE_REPURCHASE")[0]
    assert row["proposed_state"] == "AMBIGUOUS"
    assert row["facts"]["contextual_subtype_requires_review"]
    assert row["facts"]["incremental_amount_as_disclosed"] is None


def test_explicit_additional_amount_remains_source_fact() -> None:
    result = parse(
        "<p>On October 30, 2013, we announced that our Board authorized "
        "the repurchase of an additional $50 million of our common stock.</p>",
        "10-K",
    )
    row = rows(result, "SHARE_REPURCHASE")[0]
    assert row["proposed_state"] == "AUTHORIZATION_INCREASE"
    assert row["facts"]["incremental_amount_as_disclosed"]["decimal"] == "50"


@pytest.mark.parametrize(
    "sentence",
    [
        "On April 9, 2003, GM, The DIRECTV Group and News Corporation Limited, "
        "or News Corporation, "
        "announced definitive agreements providing for the split-off of The DIRECTV Group.",
        "As previously announced, the Board approved a plan to separate the banking business "
        "into two publicly traded companies.",
    ],
)
def test_explicit_prior_announcement_reference_is_retained(sentence: str) -> None:
    row = rows(parse("<p>" + sentence + "</p>", "S-4"), "SPINOFF_ANNOUNCEMENT")[0]
    assert row["facts"]["recaps_prior_announcement"]
    assert not row["facts"]["registration_is_first_announcement"]


def test_documentary_subtype_conflict_is_a_suggestion_not_an_action_merge() -> None:
    result = parse(
        "<p>On October 30, 2013, we announced that our Board authorized "
        "an additional $50 million of common stock repurchases.</p>"
        "<p>On October 30, 2013, we announced that our Board authorized "
        "another repurchase of $50,000,000 of our common stock.</p>",
        "10-K",
    )
    found = rows(result, "SHARE_REPURCHASE")
    assert len(found) == 2
    for index, row in enumerate(found):
        row["accession"] = "acc"
        row["development_observation_id"] = str(index)
    links = p.documentary_links(found)
    assert len(links) == 1
    assert links[0]["status"] == "DOCUMENTARY_SUBTYPE_DISAGREEMENT_REVIEW"
    assert not links[0]["final_dedup_certified"]


def test_all_primary_and_supporting_evidence_spans_match_and_stay_uncertified() -> None:
    result = parse(
        "<p>The Board approved a $15 million stock repurchase program. "
        "Under the new Repurchase Program, the Company may repurchase $15 million "
        "of common stock.</p>"
    )
    row = rows(result, "SHARE_REPURCHASE")[0]
    assert not row["certified_qualifying"] and row["review_status"] == "PENDING"
    for proof in [row["evidence"], *row["additional_evidence"]]:
        text = result["text"][proof["text_start"] : proof["text_end"]]
        assert proof["evidence_sha256"] == p.base.digest(text.encode())


@pytest.mark.parametrize(
    "sentence",
    [
        "The Board announced a plan to spin off its business; funds from operations "
        "will be determined separately.",
        "The Company intends to spin off its business and funds from operations "
        "will be determined separately.",
    ],
)
def test_operative_plan_survives_following_financial_definition(sentence: str) -> None:
    result = parse("<p>" + sentence + "</p>", "S-4")
    assert rows(result, "SPINOFF_ANNOUNCEMENT")


def test_board_approved_compensation_definition_is_not_a_spin_plan() -> None:
    result = parse(
        "<p>The Board approved a compensation policy; the computation of funds from operations "
        "will be adjusted from the date of the spin-off.</p>",
        "S-4",
    )
    assert not rows(result, "SPINOFF_ANNOUNCEMENT")
