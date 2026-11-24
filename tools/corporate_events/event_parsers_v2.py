"""Versioned, outcome-blind development parser; the registered v1 stays immutable."""

from __future__ import annotations

import copy
import importlib.util
import re
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "cea_v2_frozen_parser", ROOT / "tools/corporate_events/event_parsers.py"
)
assert spec and spec.loader
base: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)
VERSION = "event-parsers-development-v2.4"
ISSUER_FORMS = {"8-K", "8-K/A", "10-K", "10-K/A", "10-Q", "10-Q/A"}
SPIN_FORMS = ISSUER_FORMS | {
    "10-12B",
    "10-12B/A",
    "10-12G",
    "10-12G/A",
    "S-1",
    "S-1/A",
    "S-4",
    "S-4/A",
    "DEF 14A",
    "DEFM14A",
    "PRE 14A",
    "PREM14A",
}
BENEFICIAL = {"INITIAL_13D", "AMENDED_13D", "PASSIVE_13G", "AMENDED_13G"}
DATE = r"(?:[A-Za-z]{3,9}\.?\s+[0-9]{1,2},?\s+[0-9]{4}|[0-9]{4}-[0-9]{2}-[0-9]{2})"
MONEY = r"\$\s*([0-9][0-9,.]*)(?:\s*(million|billion|thousand))?"
SPIN = (
    "spin[ -]?off|split[ -]?off|carve[ -]?out|separat\\w*\\s+("
    "?:its |the |our )?(?:\\w+\\s+){0,5}business"
)
REP = r"repurchas\w*|buy[ -]?back"
MAX_BLOCK = 6000


class NarrativeText(HTMLParser):
    """Inert text renderer; suppress structural contexts, never execute source code."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "ix:hidden", "ix:header", "xbrli:context", "xbrli:unit"}:
            self.suppressed.append(tag)
        if not self.suppressed:
            if tag in {"p", "div", "br", "tr", "li", "h1", "h2", "h3"}:
                self.parts.append("\n")
            elif tag in {"td", "th"}:
                self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self.suppressed:
            if tag == self.suppressed[-1]:
                self.suppressed.pop()
            return
        if tag in {"p", "div", "tr", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            self.parts.append(data)


def render(raw: bytes) -> tuple[str, str]:
    original, encoding = base.decode(raw)
    parser = NarrativeText()
    parser.feed(original)
    text = "\n".join(
        clean
        for part in "".join(parser.parts).split("\n")
        if (clean := re.sub(r"\s+", " ", part).strip())
    )
    return text, str(encoding)


def evidence(doc: Any, text: str, start: int, end: int) -> dict[str, Any]:
    proof = dict(base.evidence(doc, text, start, end))
    proof["extractor_version"] = VERSION
    proof["evidence_id"] = base.stable(
        doc.source_id, VERSION, start, end, proof["derived_text_sha256"]
    )
    return proof


def blocks(text: str) -> list[tuple[int, int]]:
    """Sentence spans retain prefixes/negation and join line-broken clauses."""
    result = []
    start = 0
    for boundary in re.finditer(
        r"(?<=[.!?])\s+(?=[A-Z(])|\n(?=(?:X|\+ Details|Name:|Namespace Prefix:)\n)", text
    ):
        end = boundary.start()
        if re.search(
            r"\b(?:Inc|Corp|Co|Ltd|Mr|Mrs|Ms|Dr|Jr|Sr|U\.S|L\.P)\.$",
            text[max(0, end - 20) : end],
            re.I,
        ):
            continue
        if text[start:end].strip():
            result.append((start, end))
        start = boundary.end()
    if text[start:].strip():
        result.append((start, len(text)))
    return result


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def context_reason(text: str) -> str | None:
    low = clean(text).lower()
    if re.search(
        r'namespace prefix:|x - definition|data type:|"documentation"\s*:|us-gaap_[a-z]|srt_[a-z]',
        low,
    ):
        return "TAXONOMY_OR_STRUCTURAL_TEXT"
    if re.search(
        r"(?:in the event (?:of|that)|^if |antidilution|anti-dilution|shall (?:equitably )?adjust)",
        low,
    ):
        return "CONTRACTUAL_CONTINGENCY"
    if re.search(
        (
            "separate existence|separate stationery|separation from "
            "service|termination of service|sales organization is se"
            "parated|separation products"
        ),
        low,
    ):
        return "NON_EVENT_MEANING"
    return None


def action_date(text: str, verbs: str) -> str | None:
    direct = base.named_date(clean(text), verbs)
    if direct:
        return str(direct)
    before = re.search(r"\bon\s+(" + DATE + r").{0,180}?\b(?:" + verbs + r")\b", clean(text), re.I)
    return str(base.named_date("declared " + before[1], "declared")) if before else None


def amounts(text: str) -> list[dict[str, Any]]:
    result = []
    for match in re.finditer(MONEY, text, re.I):
        value = {
            "currency_symbol": "$",
            "decimal": base.decimal_text(match[1]),
            "scale": match[2].lower() if match[2] else None,
            "currency": None,
        }
        if value not in result:
            result.append(value)
    return result


def observation(
    family: str, state: str, proof: dict[str, Any], facts: dict[str, Any], *, candidate: bool = True
) -> dict[str, Any]:
    result: dict[str, Any] = base.proposal(
        family, state, "v2-source:" + proof["evidence_id"], facts, proof, candidate=candidate
    )
    result["development_parser_version"] = VERSION
    result["additional_evidence"] = []
    result["action_anchor_status"] = "PROVISIONAL_DOCUMENTARY_IDENTITY_NOT_FINAL_DEDUP"
    result["public_time_status"] = "TIMESTAMP_UNRESOLVED"
    return result


def activist(
    doc: Any, text: str, canonical: str, issuer: str | None, roles: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    exclusions: list[dict[str, Any]] = []
    raw_form = doc.form.upper()
    body_forms = {
        "SC 13D",
        "SCHEDULE 13D",
        "SC 13D/A",
        "SCHEDULE 13D/A",
        "SC 13G",
        "SC 13G/A",
        "SCHEDULE 13G",
        "SCHEDULE 13G/A",
    }
    if raw_form not in body_forms or re.search(r"joint filing agreement", text[:500], re.I):
        return [], [{"reason": "SUPPORTING_COMPONENT_NOT_INDEPENDENT_ITEM4"}]
    markers = list(
        re.finditer(
            r"(?im)^\s*item\s*4\b\s*[.:-]?\s*(?:purpose of (?:the )?transaction)?",
            text,
        )
    )
    sections = []
    for marker in markers:
        if re.match(r"(?:of|under|to|hereof)\b", text[marker.end() :], re.I):
            continue
        stop = re.search(r"\bitem\s*[567]\b", text[marker.end() :], re.I)
        end = marker.end() + stop.start() if stop else len(text)
        value = clean(text[marker.end() : end])
        # Ignore TOC/link references that lead to another Item4 rather than purpose prose.
        if len(value) < 80 or re.match(r"[\d.\s]+$", value):
            continue
        sections.append((marker.start(), end))
    unique = {clean(text[start:end]).lower(): (start, end) for start, end in sections}
    sections = list(unique.values())
    if not sections:
        return [], [{"reason": "ITEM4_SCOPE_UNRESOLVED_NO_HEADER_SUBSTITUTE"}]
    observations = []
    for start, end in sections:
        item = clean(text[start:end])
        sentences = re.split(r"(?<=[.;])\s+", item)
        affirmative = False
        reserved = False
        nonactivist = False
        for sentence in sentences:
            low = sentence.lower()
            modal = bool(re.search(r"\b(?:may|might|could)\b|reserv\w*.{0,25}right", low))
            reserved |= modal and bool(
                re.search(
                    r"communicat|discuss|nominat|propos|seek|change|management|board|control", low
                )
            )
            nonactivist |= bool(
                re.search(
                    (
                        "investment purposes only|no (?:present )?plans or propo"
                        "sals|not (?:seeking|intend\\w*.{0,15}) to (?:influence|c"
                        "hange).{0,30}control"
                    ),
                    low,
                )
            )
            if not modal:
                affirmative |= bool(
                    re.search(
                        (
                            "\\b(?:intend\\w* to|plan\\w* to|seek\\w* to|propos\\w* to|de"
                            "mand\\w*|request\\w*).{0,160}\\b(?:nominat\\w*|replace|elec"
                            "t|board|directors|strategic review|capital allocation|c"
                            "ontrol)"
                        ),
                        low,
                    )
                )
                affirmative |= bool(
                    re.search(
                        (
                            "\\b(?:has|have|holds?) (?:the )?right to.{0,65}(?:design"
                            "ate|appoint|nominate).{0,90}(?:board|directors)"
                        ),
                        low,
                    )
                )
        state = "INTENT_UNRESOLVED"
        if canonical == "INITIAL_13D" and len(sections) == 1:
            if affirmative and not nonactivist:
                state = "ACTIVIST_INTENT_SUPPORTED"
            elif nonactivist and not affirmative and not reserved:
                state = "NONACTIVIST_INITIAL_13D"
        facts = {
            "subject_issuer_cik": base.cik(roles.get("subject_cik_raw")),
            "reporting_filer_cik": base.cik(roles.get("filer_cik_raw")),
            "filer_cik_from_index": issuer,
            "role_resolution": "HEADER_ROLES_RESOLVED"
            if roles.get("subject_cik_raw") and roles.get("filer_cik_raw")
            else "SUBJECT_AND_REPORTER_ROLES_REQUIRE_REVIEW",
            "initial_filing_only": canonical == "INITIAL_13D",
            "item4_sections_found": len(sections),
            "other_section_or_exhibit_corroboration_required": bool(
                re.search(
                    r"except as described|exhibits? attached|incorporat\w* by reference", item, re.I
                )
            ),
            "reserved_rights_language": reserved,
            "affirmative_intent_language": affirmative,
            "investment_or_no_present_plan_language": nonactivist,
            "ownership_extraction_status": "UNRESOLVED_REVIEW_ORIGINAL_COVER_PAGES",
            "overlapping_holdings_summed": False,
            "reporting_persons": [],
            "ownership_entries": [],
        }
        observations.append(
            observation(
                "ACTIVIST_13D",
                state,
                evidence(doc, text, start, end),
                facts,
                candidate=canonical == "INITIAL_13D",
            )
        )
    return observations, exclusions


def repurchase(
    text: str, proof: dict[str, Any], issuer: str | None, kind: str
) -> dict[str, Any] | None:
    low = clean(text).lower()
    if not re.search(REP, low) or not re.search(r"stock|shares|treasury|program", low):
        return None
    if re.search(
        (
            "accounted for|we record|record repurchases|could limit|"
            "may restrict|restrict\\w*.{0,50}repurchas"
        ),
        low,
    ):
        return None
    approval = bool(
        re.search(
            r"(?:board|directors).{0,180}(?:approv\w*|authoriz\w*|authoris\w*)"
            r"|(?:company|we).{0,180}(?:approved|authorized|authorised)\b",
            low,
        )
    )
    actual = bool(
        re.search(
            (
                "\\b(?:we|company)\\s+(?:has |had |have )?(?:repurchased|p"
                "urchased)\\b|shares (?:were |have been )?repurchased"
            ),
            low,
        )
    )
    asr = "accelerated share repurchase" in low and bool(
        re.search(r"entered|agreement|executed", low)
    )
    tender = "tender offer" in low and bool(re.search(r"commenc|announc|launch", low))
    extension = bool(re.search(r"extend(?:ed|s|ing).{0,80}(?:program|authorization|expiry)", low))
    expired = bool(re.search(r"program.{0,40}(?:has |had )?expired", low))
    completed = bool(re.search(r"program.{0,40}(?:completed|completion)", low))
    if not any((approval, actual, asr, tender, extension, expired, completed)):
        return None
    no_expiration = bool(
        re.search(
            (
                "no (?:stated )?(?:expiration|expiry|time limit)|does no"
                "t (?:have|expire)|without (?:an? )?(?:expiration|time l"
                "imit)"
            ),
            low,
        )
    )
    negated = bool(
        re.search(r"(?:not|no)\s+(?:approved|authorized|authorised|new repurchase)", low)
    )
    renewal = bool(re.search(r"renew(?:al|ed)|replac\w*.{0,35}program", low))
    extra = bool(
        re.search(
            (
                "(?:additional|increas\\w*|expand\\w*).{0,70}(?:authorizat"
                "ion|program|repurchas)|authoriz\\w*.{0,60}additional"
            ),
            low,
        )
    )
    state = "AMBIGUOUS"
    explicit_new = bool(re.search(r"new|additional", low))
    existing = bool(re.search(r"previously (?:approved|authorized)|existing program", low))
    if not negated:
        if asr and not explicit_new:
            state = "ASR_AGREEMENT"
        elif tender and not approval:
            state = "TENDER_OFFER"
        elif approval and extra:
            state = "AUTHORIZATION_INCREASE"
        elif extension and not explicit_new:
            state = "EXTENSION_ONLY"
        elif expired and not no_expiration:
            state = "EXPIRATION"
        elif completed:
            state = "COMPLETION"
        elif approval and not renewal and not existing:
            state = "NEW_AUTHORIZATION"
        elif actual:
            state = "ROUTINE_ACTUAL_REPURCHASES"
    capacity_text = text
    actual_clause = re.search(
        r"\b(?:we|company)\s+(?:has |had |have )?(?:repurchased|purchased)\b", text, re.I
    )
    if approval and actual_clause:
        capacity_text = text[: actual_clause.start()]
    capacity = amounts(capacity_text) if approval else []
    date = action_date(text, r"approved|authorized|authorised") if approval else None
    facts = {
        "issuer_cik_from_index": issuer,
        "approval_date": date,
        "authorization_amounts_as_disclosed": capacity,
        "incremental_amount_as_disclosed": capacity[0] if extra and len(capacity) == 1 else None,
        "incremental_amount_inferred": False,
        "authorization_vs_actuals": "AUTHORIZATION" if approval else "ACTUAL_EXECUTION_RECAP",
        "actuals_also_in_passage": actual,
        "separate_asr_component": asr,
        "separate_tender_component": tender,
        "renewal_requires_capacity_review": renewal,
        "open_market_permission": bool(re.search(r"open[ -]market", low)),
        "expiration_status": "NO_STATED_EXPIRATION" if no_expiration else "UNRESOLVED",
        "expiration_date": None
        if no_expiration
        else base.named_date(clean(text), r"expires|expiration date(?: is)?"),
        "chronology_requires_review": kind == "PERIODIC" or "previously" in low or actual,
        "sec_class_label": "COMMON" if re.search(r"common (?:stock|shares)", low) else None,
        "capacity_and_action_identity_require_review": len(capacity) != 1 or date is None,
    }
    return observation("SHARE_REPURCHASE", state, proof, facts)


def dividend(text: str, proof: dict[str, Any], issuer: str | None) -> dict[str, Any] | None:
    low = clean(text).lower()
    if "dividend" not in low:
        return None
    if re.search(
        (
            "dividend (?:equivalents|income|costs)|preference shares"
            "|preferred (?:stock|shares)|subsidiar\\w*.{0,80}dividend"
        ),
        low,
    ):
        return None
    if re.search(
        (
            "never (?:declared|paid)|(?:not|no)\\s+(?:declared|pay|di"
            "vidend)|no (?:present )?(?:plan|intention).{0,35}divide"
            "nd"
        ),
        low,
    ):
        return None
    if not re.search(
        (
            "\\b(?:declared|approved|declares|increased|initiated|res"
            "umed|suspended)\\b.{0,160}\\b(?:cash )?dividend|\\bdividen"
            "d.{0,100}\\b(?:declared|approved)"
        ),
        low,
    ):
        return None
    if not re.search(r"board|directors|company|\bwe\b", low):
        return None
    matches = list(re.finditer(MONEY, text, re.I))
    amounts_seen = [base.decimal_text(m[1]) for m in matches]
    target = re.search(r"from\s*\$[0-9,.]+\s*(?:per share\s*)?to\s*\$\s*([0-9,.]+)", text, re.I)
    amount = (
        base.decimal_text(target[1])
        if target
        else amounts_seen[0]
        if len(amounts_seen) == 1
        else None
    )
    per_share = bool(re.search(r"per (?:common )?share", low))
    if not per_share:
        amount = None
    frequency = re.findall(r"\b(monthly|quarterly|semiannual|annual)\b", low)
    special = bool(re.search(r"\bspecial\s+(?:cash )?dividend", low))
    state = (
        "SPECIAL_DIVIDEND"
        if special
        else "SUSPENSION"
        if "suspended" in low
        else "RESUMPTION"
        if "resumed" in low
        else "UNKNOWN_BASELINE"
    )
    facts = {
        "issuer_cik_from_index": issuer,
        "declared_cash_per_share_decimal": amount,
        "amount_candidates_as_disclosed": amounts_seen,
        "per_share_denominator": "ONE_SHARE" if per_share else None,
        "currency": "USD" if re.search(r"US\$|U\.S\. dollars|\bUSD\b", text, re.I) else None,
        "sec_class_label": "COMMON" if re.search(r"common (?:stock|shares)", low) else None,
        "frequency": frequency[0] if frequency and len(set(frequency)) == 1 else None,
        "regular_or_special": "SPECIAL"
        if special
        else "REGULAR"
        if re.search(r"regular|quarterly|monthly|semiannual|annual", low)
        else "REGULAR_REQUIRES_REVIEW",
        "declaration_date": action_date(text, r"declared|declares|approved|increased|initiated"),
        "record_date": base.named_date(
            clean(text),
            r"(?:shareholders|stockholders) of record(?: at the close of business)?(?: on| as of)?",
        ),
        "payment_date": base.named_date(clean(text), r"payable(?: on)?|paid(?: on)?"),
        "source_reports_increase": bool(
            re.search(r"\bincreas\w*.{0,60}dividend|dividend.{0,60}\bincreas\w*", low)
        ),
        "baseline_status": "UNKNOWN_BASELINE",
        "baseline_source_ids": [],
        "left_censored_history": not special,
        "no_historical_change_inferred": True,
    }
    return observation("DIVIDEND_POSITIVE_CHANGE", state, proof, facts)


def spin(
    text: str, proof: dict[str, Any], issuer: str | None, doc_kind: str
) -> dict[str, Any] | None:
    low = clean(text).lower()
    if not re.search(SPIN, low):
        return None
    # Completion conditions, assumptions and future consequences are not completed acts.
    completed = False
    past = (
        r"\b(?:completed|consummated)\b.{0,70}(?:spin|separat)"
        r"|(?:spin|separat).{0,80}\b(?:was|has been|had been) (?:completed|consummated)"
        r"|\b(?:spun|split)[ -]off from"
    )
    for match in re.finditer(past, low):
        prefix = low[max(0, match.start() - 90) : match.start()]
        if not re.search(
            r"(?:will|would|may|could|expected to|expected to be|not|after|"
            r"upon|once|following|until|assuming)\b[^.]*$",
            prefix,
        ):
            completed = True
    cancelled = bool(
        re.search(
            r"(?:has|had|was|board|company|parent)\s+(?:\w+\s+){0,4}"
            r"(?:cancelled|canceled|abandoned)|announced.{0,60}cancellation",
            low,
        )
    ) and not re.search(r"(?:not|may|might|could|will)\b.{0,50}cancel|if.{0,40}cancel", low)
    intention = bool(
        re.search(
            r"(?:board|directors).{0,180}(?:approved|determined).{0,200}"
            r"(?:separat|spin|publicly traded)"
            r"|\b(?:intend(?:s)? to|plans? to|planned|proposed|agreed to)\b.{0,120}(?:spin|separat)"
            r"|\bwill\s+(?:be\s+)?(?:spin[ -]?off|separate|distribute|effect)"
            r"|definitive agreements.{0,320}(?:spin|separat)"
            r"|spin.{0,80}distribution",
            low,
        )
    )
    if re.search(r"strategic alternatives|potential (?:sale|spin)|possible (?:sale|spin)", low):
        state = "STRATEGIC_ALTERNATIVES_REVIEW"
    elif cancelled:
        state = "CANCELLED_PROPOSAL"
    elif completed:
        state = "COMPLETION_ONLY_RECAP"
    elif intention:
        state = "SPINOFF_INTENTION"
    elif re.search(r"(?:announced|agreed|completed|provide\w*).{0,100}split[ -]?off", low):
        state = "SPLIT_OFF"
    elif re.search(r"(?:announced|approved|plan\w*|completed).{0,120}carve[ -]?out", low):
        state = "CARVE_OUT_IPO"
    else:
        return None
    facts = {
        "filer_cik_from_index": issuer,
        "parent_cik_from_index": None,
        "parent_role_status": "TRANSACTION_PARENT_REQUIRES_DOCUMENTARY_IDENTITY",
        "spinco_identity": None,
        "source_component_role": "SUPPORTING_CONTRACT"
        if doc_kind == "EXHIBIT" and re.search(r"whereas|agreement|pursuant", low)
        else "NARRATIVE_DISCLOSURE",
        "pro_rata_distribution_stated": "pro rata" in low,
        "first_disclosure_status": "LEFT_CENSORED_HISTORY",
        "registration_is_first_announcement": False,
        "earliest_source_search_complete": False,
        "recaps_prior_announcement": bool(
            re.search(r"previously announced|on " + DATE.lower() + r".{0,50}announced", low)
        ),
        "completion_status": "STATED_COMPLETED" if completed else "UNRESOLVED_OR_PROPOSED",
        "future_completion_used": False,
        "future_cancellation_used": False,
        "structure_from_sec_locator": proof["locator"],
    }
    return observation("SPINOFF_ANNOUNCEMENT", state, proof, facts)


def consolidate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge exact repeats or explicit same-date capacity/rate facts within one component."""
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        facts = row["facts"]
        key: Any = (
            "EXACT",
            row["family"],
            row["proposed_state"],
            row["evidence"]["evidence_sha256"],
        )
        if (
            row["family"] == "SHARE_REPURCHASE"
            and facts.get("approval_date")
            and len(facts.get("authorization_amounts_as_disclosed", [])) == 1
            and facts.get("sec_class_label")
        ):
            key = (
                "CAPACITY",
                row["proposed_state"],
                facts["approval_date"],
                facts["authorization_amounts_as_disclosed"],
                facts["sec_class_label"],
            )
        elif row["family"] == "DIVIDEND_POSITIVE_CHANGE" and all(
            facts.get(k)
            for k in (
                "declaration_date",
                "declared_cash_per_share_decimal",
                "sec_class_label",
                "frequency",
            )
        ):
            key = (
                "DECLARATION",
                row["proposed_state"],
                *(
                    facts[k]
                    for k in (
                        "declaration_date",
                        "declared_cash_per_share_decimal",
                        "sec_class_label",
                        "frequency",
                    )
                ),
            )
        identifier = base.stable(key)
        if identifier in groups:
            groups[identifier]["additional_evidence"].append(row["evidence"])
            groups[identifier]["merge_status"] = (
                "PROVISIONAL_SAME_DOCUMENT_FACT_NOT_FINAL_ACTION_DEDUP"
            )
        else:
            groups[identifier] = row
    return list(groups.values())


def narrative(
    doc: Any, canonical_form: str, issuer_cik: str | None, *, roles: dict[str, Any] | None = None
) -> dict[str, Any]:
    raw_prefix = base.decode(doc.raw[:4000])[0]
    probe = re.sub(r"^\s*<XBRL>\s*", "", raw_prefix, flags=re.I)
    probe = re.sub(r"(?s)^\s*(?:<\?.*?\?>|<!--.*?-->|<!DOCTYPE[^>]*>)\s*", "", probe)
    for _ in range(10):
        shortened = re.sub(r"(?s)^\s*(?:<\?.*?\?>|<!--.*?-->)\s*", "", probe)
        if shortened == probe:
            break
        probe = shortened
    inline_html = bool(re.match(r"\s*<html\b", probe, re.I))
    non_narrative = bool(
        re.search(r"\.(?:json|xsd|js|css|jpg|jpeg|gif|png|pdf|xls|xlsx|zip)$", doc.filename, re.I)
        or (
            not inline_html
            and re.search(r"<(?:\w+:)?xbrl(?:\s|>)|<schema(?:\s|>)", raw_prefix, re.I)
        )
    )
    text, encoding = ("", "UNRENDERED") if non_narrative else render(doc.raw)
    rows: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    if non_narrative:
        exclusions.append({"reason": "NON_NARRATIVE_OR_EMBEDDED_COMPONENT_RETAINED_UNRESOLVED"})
    elif canonical_form in BENEFICIAL:
        rows, exclusions = activist(doc, text, canonical_form, issuer_cik, roles or {})
    else:
        spans = blocks(text)
        for index, (start, end) in enumerate(spans):
            passage = text[start:end]
            if not re.search(REP + "|" + SPIN + r"|dividend|\bseparat\w*\b", passage, re.I):
                continue
            reason = context_reason(passage)
            if reason or len(passage) > MAX_BLOCK:
                exclusions.append(
                    {
                        "reason": reason or "OVERSIZED_TEXT_BLOCK_REVIEW",
                        "evidence": evidence(doc, text, start, end),
                    }
                )
                continue
            # Attach one payment/record sentence only to the immediately preceding declaration.
            dividend_end = end
            if re.search(r"\bdeclar\w*\b", passage, re.I) and index + 1 < len(spans):
                following = text[spans[index + 1][0] : spans[index + 1][1]]
                if (
                    re.match(r"(?:the |this )?dividend\b", following.strip(), re.I)
                    and re.search(r"payable|record", following, re.I)
                    and not re.search(r"\bdeclar\w*\b", following, re.I)
                ):
                    dividend_end = spans[index + 1][1]
            found = []
            proof = evidence(doc, text, start, end)
            if canonical_form in ISSUER_FORMS:
                found.extend(
                    [
                        repurchase(passage, proof, issuer_cik, doc.kind),
                        dividend(
                            text[start:dividend_end],
                            evidence(doc, text, start, dividend_end),
                            issuer_cik,
                        ),
                    ]
                )
            if canonical_form in SPIN_FORMS:
                found.append(spin(passage, proof, issuer_cik, doc.kind))
            usable = [row for row in found if row is not None]
            if not usable:
                exclusions.append(
                    {"reason": "MENTION_WITHOUT_SUPPORTED_TESTED_ACTION", "evidence": proof}
                )
            rows.extend(usable)
    return {
        "status": "DEVELOPMENT_V2_PROPOSALS_PENDING_INDEPENDENT_REVIEW",
        "text": text,
        "derived_text_sha256": base.digest(text.encode()),
        "encoding": encoding,
        "schema_validation": "NO_NEW_SCHEMA_AUTHORITY_CLAIM",
        "observations": consolidate(rows),
        "retained_contexts": exclusions,
        "context_reason_counts": dict(Counter(x["reason"] for x in exclusions)),
        "coverage_status": "UNRESOLVED_NOT_A_WHOLE_SOURCE_NEGATIVE",
        "parser_version": VERSION,
    }


def ownership(doc: Any) -> dict[str, Any]:
    """Use the frozen ownership parser without extending historical schema authority."""
    result: dict[str, Any] = copy.deepcopy(base.ownership(doc))
    result["implementation_version"] = base.VERSION
    result["schema_authority_changed"] = False
    return result


def documentary_links(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Suggest same-accession links; never merge independent disclosures automatically."""
    groups: dict[str, list[str]] = {}
    for row in rows:
        facts = row["facts"]
        family = row["family"]
        if family == "SHARE_REPURCHASE":
            day = facts.get("approval_date")
            quantity = facts.get("authorization_amounts_as_disclosed")
            valid = day and quantity and len(quantity) == 1
        elif family == "DIVIDEND_POSITIVE_CHANGE":
            day = facts.get("declaration_date")
            quantity = (facts.get("declared_cash_per_share_decimal"), facts.get("frequency"))
            valid = day and all(quantity)
        else:
            continue
        if not valid or not facts.get("issuer_cik_from_index") or not facts.get("sec_class_label"):
            continue
        key = base.stable(
            row["accession"],
            family,
            row["proposed_state"],
            base.cik(facts["issuer_cik_from_index"]),
            day,
            quantity,
            facts["sec_class_label"],
        )
        groups.setdefault(key, []).append(row["development_observation_id"])
    return [
        {
            "link_id": key,
            "members": sorted(set(value)),
            "status": "SAME_ACCESSION_DOCUMENTARY_LINK_REQUIRES_REVIEW",
            "final_dedup_certified": False,
        }
        for key, value in sorted(groups.items())
        if len(set(value)) > 1
    ]
