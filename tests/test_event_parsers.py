"""Synthetic outcome-blind family parser and evidence tests; no market observations."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "event_parsers_test", ROOT / "tools/corporate_events/event_parsers.py"
)
assert spec and spec.loader
p: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = p
spec.loader.exec_module(p)
ACC = "0000000123-24-000001"


def doc(text: str, form: str = "8-K", kind: str = "BODY") -> Any:
    raw = text.encode()
    return p.Document(ACC, "synthetic.txt", form, kind, "1", 0, len(raw), raw)


def ownership_xml(
    footnote: str = "",
    *,
    code: str = "P",
    acquired: str = "A",
    derivative: bool = False,
    rows: int = 1,
    flag: str = "false",
    version: str = "X0306",
    owners: int = 1,
) -> str:
    table, row = (
        ("derivativeTable", "derivativeTransaction")
        if derivative
        else ("nonDerivativeTable", "nonDerivativeTransaction")
    )
    owner = (
        "<reportingOwner><reportingOwnerId><rptOwnerCik>1"
        "2</rptOwnerCik><rptOwnerName>Synthetic Owner</rp"
        "tOwnerName></reportingOwnerId><reportingOwnerRel"
        "ationship><isDirector>1</isDirector><officerTitl"
        "e>Verbatim Role</officerTitle></reportingOwnerRe"
        "lationship></reportingOwner>"
    )
    transaction = (
        "<securityTitle><value>Class A Common Stock</valu"
        "e></securityTitle><transactionDate><value>2024-0"
        "1-01</value></transactionDate><transactionCoding"
        "><transactionCode>"
        f"{code}"
        "</transactionCode></transactionCoding><transacti"
        "onAmounts><transactionShares><value>7</value></t"
        "ransactionShares><transactionAcquiredDisposedCod"
        "e><value>"
        f"{acquired}"
        "</value></transactionAcquiredDisposedCode></tran"
        "sactionAmounts><ownershipNature><directOrIndirec"
        "tOwnership><value>D</value></directOrIndirectOwn"
        "ership></ownershipNature><footnoteId id='F1'/>"
    )
    return (
        (
            "<ownershipDocument><schemaVersion>"
            f"{version}"
            "</schemaVersion><issuer><issuerCik>123</issuerCi"
            "k><issuerName>Synthetic Issuer</issuerName></iss"
            "uer><aff10b5One>"
            f"{flag}"
            "</aff10b5One>"
            f"{owner * owners}"
            "<"
            f"{table}"
            ">"
        )
        + (f"<{row}>{transaction}</{row}>" * rows)
        + (
            "</"
            f"{table}"
            "><footnotes><footnote id='F1'>"
            f"{footnote}"
            "</footnote></footnotes></ownershipDocument>"
        )
    )


def first_owner(text: str, form: str = "4") -> dict[str, Any]:
    return dict(p.ownership(doc(text, form, "OWNERSHIP_XML"))["observations"][0])


def narrative(text: str, canonical: str = "8-K") -> list[dict[str, Any]]:
    return list(p.narrative(doc(text), canonical, "123")["observations"])


def family(rows: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [row for row in rows if row["family"] == name]


def test_sgml_resolution_preserves_bytes_and_acceptance_without_public_time() -> None:
    raw = (
        "<SEC-DOCUMENT>"
        f"{ACC}"
        ".txt\n<SEC-HEADER>\nACCESSION NUMBER: "
        f"{ACC}"
        "\n<ACCEPTANCE-DATETIME>20241104163000\n<DOCUMENT>\n"
        "<TYPE>8-K\n<SEQUENCE>1\n<FILENAME>body.htm\n<TEXT>\n"
        "<p>Synthetic source</p>\n</TEXT>\n</DOCUMENT>\n<DOC"
        "UMENT>\n<TYPE>EX-99.1\n<SEQUENCE>2\n<FILENAME>relea"
        "se.htm\n<TEXT>\nExhibit source\n</TEXT>\n</DOCUMENT>"
        "\n"
    ).encode()
    docs, header = p.resolve_submission(raw, ACC)
    assert len(docs) == 2 and docs[1].kind == "EXHIBIT"
    assert all(raw[d.start : d.end] == d.raw for d in docs)
    assert header["acceptance_raw"] == "20241104163000"
    assert "public_upper_utc" not in header
    with pytest.raises(ValueError, match="ACCESSION_MISMATCH"):
        p.resolve_submission(raw, "0000000123-24-000002")


def test_html_is_visible_text_only() -> None:
    text, _ = p.text_document(
        b"<p>Visible declaration</p><script>invisible divi"
        b"dend</script><ix:hidden>hidden repurchase</ix:hi"
        b"dden>"
    )
    assert text == "Visible declaration"


def test_form4_candidate_requires_explicit_open_market_nonplan_common_evidence() -> None:
    row = first_owner(
        ownership_xml(
            "Purchased in the open market; not made pursuant to a Rule 10b5-1 trading plan."
        )
    )
    assert row["proposed_state"] == "OPEN_MARKET_SUPPORTED"
    assert row["facts"]["discretion_state"] == "NON_PLAN_DISCRETION_SUPPORTED"
    assert row["certified_qualifying"] is False
    assert row["facts"]["transaction_date"] == "2024-01-01"
    assert not any(
        key.lower() in {"price", "return", "car", "bhar", "permno", "gvkey"} for key in row["facts"]
    )


@pytest.mark.parametrize(
    "kwargs,state,candidate",
    [
        ({}, "DISCRETION_UNRESOLVED", True),
        ({"derivative": True}, "DERIVATIVE_OR_OTHER", False),
        ({"code": "S"}, "REJECTED_NONQUALIFYING", False),
        ({"acquired": "D"}, "REJECTED_NONQUALIFYING", False),
    ],
)
def test_form4_unknowns_and_noncandidates_retained(
    kwargs: dict[str, Any], state: str, candidate: bool
) -> None:
    row = first_owner(ownership_xml(**kwargs))
    assert row["proposed_state"] == state and row["parser_candidate"] is candidate


def test_private_purchase_and_amendment_are_not_open_market_events() -> None:
    assert (
        first_owner(ownership_xml("A private placement transaction."))["proposed_state"]
        == "PRIVATE_PURCHASE"
    )
    row = first_owner(ownership_xml("An open market purchase."), "4/A")
    assert row["proposed_state"] == "AMENDMENT_CORRECTION" and not row["parser_candidate"]


def test_plan_flag_not_smeared_across_multiple_rows_and_joint_owners_not_multiplied() -> None:
    parsed = p.ownership(doc(ownership_xml(rows=2, flag="true", owners=2), "4", "OWNERSHIP_XML"))
    assert len(parsed["observations"]) == 2
    assert all(
        row["facts"]["discretion_state"] == "DISCRETION_UNRESOLVED"
        for row in parsed["observations"]
    )
    assert len(parsed["reporting_owners"]) == 2
    assert first_owner(ownership_xml(flag="true"))["proposed_state"] == "PLAN_TRANSACTION"
    row = first_owner(ownership_xml("Pursuant to a 10b5-1 trading plan adopted on 2023-03-01."))
    assert row["facts"]["plan_adoption_date"] == "2023-03-01"


def test_unknown_schema_and_entities_never_become_validated() -> None:
    parsed = p.ownership(doc(ownership_xml(version="UNKNOWN"), "4"))
    assert (
        parsed["schema_version"] == "UNKNOWN" and parsed["schema_validation"] == "NOT_XSD_VALIDATED"
    )
    parsed = p.ownership(
        doc("<!DOCTYPE x [<!ENTITY e 'x'>]><ownershipDocument>&e;</ownershipDocument>", "4")
    )
    assert parsed["status"] == "SCHEMA_OR_XML_UNRESOLVED"


@pytest.mark.parametrize(
    "text,canonical,state,candidate",
    [
        (
            (
                "Item 4. Purpose of Transaction. The reporting pe"
                "rsons intend to nominate directors to the board."
                " Item 5. Holdings."
            ),
            "INITIAL_13D",
            "ACTIVIST_INTENT_SUPPORTED",
            True,
        ),
        (
            "Item 4. Purpose of Transaction. For investment purposes only. Item 5. Holdings.",
            "INITIAL_13D",
            "NONACTIVIST_INITIAL_13D",
            True,
        ),
        (
            (
                "Item 4. Purpose of Transaction. The persons may "
                "seek to change control and may discuss strategic"
                " matters. Item 5. Holdings."
            ),
            "INITIAL_13D",
            "INTENT_UNRESOLVED",
            True,
        ),
        (
            "Item 4. Purpose of Transaction. They intend to nominate directors. Item 5. Holdings.",
            "AMENDED_13D",
            "INTENT_UNRESOLVED",
            False,
        ),
        (
            "Item 4. Purpose of Transaction. For investment purposes only. Item 5. Holdings.",
            "PASSIVE_13G",
            "INTENT_UNRESOLVED",
            False,
        ),
    ],
)
def test_13d_states_use_initial_item4_only(
    text: str, canonical: str, state: str, candidate: bool
) -> None:
    row = family(narrative(text, canonical), "ACTIVIST_13D")[0]
    assert row["proposed_state"] == state and row["parser_candidate"] is candidate
    assert row["facts"]["overlapping_holdings_summed"] is False


@pytest.mark.parametrize(
    "text,state",
    [
        (
            (
                "The board approved a new stock repurchase progra"
                "m of up to $100 million USD in open market trans"
                "actions."
            ),
            "NEW_AUTHORIZATION",
        ),
        (
            (
                "The board increased the stock repurchase authori"
                "zation by an additional $100 million USD."
            ),
            "AUTHORIZATION_INCREASE",
        ),
        (
            (
                "Item 703. Shares were repurchased during the qua"
                "rter under a previously authorized stock repurch"
                "ase program."
            ),
            "ROUTINE_ACTUAL_REPURCHASES",
        ),
        ("The board extended the expiration of the stock repurchase program.", "EXTENSION_ONLY"),
        (
            "The company entered an accelerated share repurchase agreement for common stock.",
            "ASR_AGREEMENT",
        ),
    ],
)
def test_repurchase_context_is_not_new_capacity(text: str, state: str) -> None:
    rows = family(narrative(text), "SHARE_REPURCHASE")
    assert rows[0]["proposed_state"] == state and rows[0]["certified_qualifying"] is False
    assert rows[0]["facts"]["incremental_amount_inferred"] is False


def test_repurchase_does_not_structure_a_documentary_per_share_price_as_authorization() -> None:
    row = family(
        narrative(
            "The stock repurchase program repurchased shares "
            "at an average purchase price of $12 per share."
        ),
        "SHARE_REPURCHASE",
    )[0]
    assert row["facts"]["authorization_amounts_as_disclosed"] == []


def declaration(**kwargs: Any) -> dict[str, Any]:
    return {
        "source_id": "source-a",
        "issuer_cik": "0000000123",
        "sec_class_label": "COMMON",
        "currency": "USD",
        "frequency": "QUARTERLY",
        "per_share_denominator": "ONE_SHARE",
        "regular_or_special": "REGULAR",
        "declared_cash_per_share_decimal": "0.20",
        "public_lower_utc": "2020-02-01T12:00:00Z",
        "public_upper_utc": "2020-02-01T12:00:00Z",
        **kwargs,
    }


def test_dividend_baseline_uses_prior_comparable_regular_not_special_or_future() -> None:
    prior = declaration(
        public_upper_utc="2019-12-01T12:00:00Z", declared_cash_per_share_decimal="0.10"
    )
    special = declaration(
        source_id="special",
        regular_or_special="SPECIAL",
        public_upper_utc="2020-01-01T12:00:00Z",
        declared_cash_per_share_decimal="1.00",
    )
    future = declaration(source_id="future", public_upper_utc="2020-03-01T12:00:00Z")
    result = p.comparable_dividend(declaration(), [prior, special, future])
    assert result["state"] == "DIVIDEND_INCREASE" and result["baseline_source_id"] == "source-a"
    assert p.comparable_dividend(declaration(), [future])["state"] == "UNKNOWN_BASELINE"
    assert (
        p.comparable_dividend(declaration(frequency="ANNUAL"), [prior])["state"]
        == "UNKNOWN_BASELINE"
    )
    assert (
        p.comparable_dividend(declaration(sec_class_label="CLASS_B"), [prior])["state"]
        == "UNKNOWN_BASELINE"
    )


def test_dividend_missing_history_not_initiation_and_resumption_not_first_ever() -> None:
    assert p.comparable_dividend(declaration(), [])["state"] == "UNKNOWN_BASELINE"
    current = declaration(
        positive_initiation_evidence_id="explicit-first",
        prehistory_evidence_ids=["prehistory"],
        consistent_prehistory_verified=True,
    )
    assert p.comparable_dividend(current, [])["state"] == "DIVIDEND_INITIATION"
    assert (
        p.comparable_dividend(declaration(context_state="RESUMPTION"), [])["state"] == "RESUMPTION"
    )
    assert (
        p.comparable_dividend(declaration(regular_or_special="SPECIAL"), [])["state"]
        == "SPECIAL_DIVIDEND"
    )
    assert (
        p.comparable_dividend(declaration(public_lower_utc=None), [])["state"] == "UNKNOWN_BASELINE"
    )


def test_regular_and_special_components_separate_without_initiation_inference() -> None:
    rows = family(
        narrative(
            "The board declared a quarterly cash dividend of "
            "$0.20 USD and a special cash dividend of $1.00 U"
            "SD."
        ),
        "DIVIDEND_POSITIVE_CHANGE",
    )
    assert len(rows) == 2
    assert {row["proposed_state"] for row in rows} == {"UNKNOWN_BASELINE", "SPECIAL_DIVIDEND"}


def test_late_form10_and_later_cancellation_do_not_rewrite_intent() -> None:
    initial = family(
        narrative(
            "The board plans to spin off the subsidiary through a distribution to shareholders.",
            "10-12B",
        ),
        "SPINOFF_ANNOUNCEMENT",
    )[0]
    assert initial["proposed_state"] == "SPINOFF_INTENTION"
    assert initial["facts"]["first_disclosure_status"] == "LEFT_CENSORED_HISTORY"
    later = family(
        narrative("The company cancelled the spin-off proposal."), "SPINOFF_ANNOUNCEMENT"
    )[0]
    assert later["proposed_state"] == "CANCELLED_PROPOSAL"
    assert initial["proposed_state"] == "SPINOFF_INTENTION"


def test_exact_qa_rank_and_independent_control_gate() -> None:
    import hashlib

    row = {
        "family": "SHARE_REPURCHASE",
        "era": "2006-2012",
        "form": "8-K",
        "source_kind": "EXHIBIT",
        "accession": ACC,
        "source_document_sha256": "a" * 64,
        "source_id": "id",
        "role": "INDEPENDENT_SOURCE_CONTROL",
        "selected_before_candidate_filter": True,
    }
    expected = hashlib.sha256(
        (
            "corporate-event-alpha-v1|phase2-qa-v1|SHARE_REPU"
            "RCHASE|2006-2012|8-K|EXHIBIT|"
            f"{ACC}"
            "|"
            f"{'a' * 64}"
        ).encode()
    ).hexdigest()
    result = p.select_qa([row], frame_scope="DEVELOPMENT")
    assert result["selected"][0]["rank_sha256"] == expected
    assert result["cells"][0]["shortfall"] == 4 and result["final_qa_sample"] is False
    with pytest.raises(ValueError, match="INDEPENDENTLY"):
        p.select_qa([{**row, "selected_before_candidate_filter": False}], frame_scope="DEVELOPMENT")


def test_text_similarity_or_same_day_never_suffices_for_cluster_merge() -> None:
    left = {
        "issuer_id": "issuer",
        "sec_class_label": "COMMON",
        "economic_action_anchor": "action",
        "family": "SHARE_REPURCHASE",
    }
    assert p.cluster_relation(left, dict(left)) == "AMBIGUOUS"
    left["explicit_same_action_evidence_id"] = "same-action-proof"
    assert p.cluster_relation(left, dict(left)) == "LINK"
    assert p.cluster_relation(left, {**left, "family": "DIVIDEND_POSITIVE_CHANGE"}) == "DO_NOT_LINK"
