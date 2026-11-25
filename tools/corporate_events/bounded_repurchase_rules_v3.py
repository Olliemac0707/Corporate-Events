"""Conservative source-fact rules for the fixed repurchase development ledger."""

from __future__ import annotations

import importlib.util
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "bounded_repurchase_baseline", ROOT / "tools/corporate_events/event_parsers_v4.py"
)
assert spec and spec.loader
p: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = p
spec.loader.exec_module(p)
VERSION = "bounded-repurchase-rules-v3"
KEYWORD = re.compile(r"\brepurchas\w*|\bbuy[ \-\u2010\u2011\u2013\u2014]?back\b", re.I)
MAX_TEXT = 8_388_608
MAX_INPUT = 52_428_800
MAX_PASSAGE = 6000
HIDDEN = {"script", "style", "head", "ix:hidden", "ix:header", "xbrli:context", "xbrli:unit"}
BLOCKS = {
    "p",
    "div",
    "tr",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "section",
    "article",
    "table",
    "br",
    "pre",
}
VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "wbr"}


class Visible(HTMLParser):
    """Retain visible table rows and footnotes; never execute embedded content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[tuple[str, bool]] = []
        self.nodes = 0
        self.characters = 0

    def add(self, text: str) -> None:
        self.characters += len(text)
        if self.characters > MAX_TEXT:
            raise ValueError("VISIBLE_TEXT_LIMIT_SOURCE_RETAINED")
        self.parts.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        peers = (
            {"td", "th"}
            if tag in {"td", "th"}
            else {"tr"}
            if tag == "tr"
            else {"li"}
            if tag == "li"
            else {"p"}
            if tag in {"p", "div", "table"}
            else set()
        )
        barriers = (
            {"tr", "table"}
            if tag in {"td", "th"}
            else {"table"}
            if tag == "tr"
            else {"ul", "ol"}
            if tag == "li"
            else {"table", "td", "th", "body"}
        )
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] in peers:
                del self.stack[index:]
                break
            if self.stack[index][0] in barriers:
                break
        self.nodes += 1
        if self.nodes > 400_000 or len(self.stack) >= 256:
            raise ValueError("HTML_STRUCTURE_LIMIT_SOURCE_RETAINED")
        attributes = dict(attrs)
        concealed = (
            bool(self.stack and self.stack[-1][1])
            or tag in HIDDEN
            or bool(
                re.search(
                    r"display\s*:\s*none|visibility\s*:\s*hidden",
                    attributes.get("style") or "",
                    re.I,
                )
            )
            or "hidden" in attributes
        )
        if not concealed and tag in BLOCKS | {"td", "th"}:
            self.add(" " if tag in {"td", "th"} else "\n")
        if tag not in VOID:
            self.stack.append((tag, concealed))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            return
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                concealed = self.stack[index][1]
                del self.stack[index:]
                if not concealed and tag in BLOCKS | {"td", "th"}:
                    self.add(" " if tag in {"td", "th"} else "\n")
                return

    def handle_data(self, data: str) -> None:
        if not self.stack or not self.stack[-1][1]:
            self.add(re.sub(r"\s+", " ", data))


def render(doc: Any) -> dict[str, Any]:
    if re.search(
        r"\.(?:pdf|png|jpg|jpeg|gif|zip|xls|xlsx|xsd|js|css)$", doc.filename, re.I
    ) or doc.form.upper() in {"GRAPHIC", "ZIP", "PDF", "EXCEL"}:
        return {"status": "NON_TEXT_COMPONENT_RETAINED", "text": "", "encoding": None}
    original, encoding = p.base.decode(doc.raw)
    if len(original) > MAX_INPUT:
        return {"status": "COMPONENT_INPUT_SIZE_LIMIT_RETAINED", "text": "", "encoding": encoding}
    if re.search(r"<(?:\w+:)?xbrl\b|<(?:\w+:)?schema\b", original[:8000], re.I) and not re.search(
        r"<html\b", original[:8000], re.I
    ):
        return {"status": "STRUCTURED_COMPONENT_RETAINED", "text": "", "encoding": encoding}
    try:
        is_html = bool(re.search(r"<[A-Za-z][^>]*>", original))
        if is_html:
            visible = Visible()
            for offset in range(0, len(original), 65536):
                visible.feed(original[offset : offset + 65536])
            visible.close()
            original = "".join(visible.parts)
        if not is_html:
            original = "\n".join(
                " ".join(block.split()) for block in re.split(r"\n\s*\n", original)
            )
        text = "\n".join(
            line
            for value in original.replace("\r", "\n").splitlines()
            if (line := re.sub(r"\s+", " ", value).strip())
        )
        if len(text) > MAX_TEXT:
            return {
                "status": "COMPONENT_VISIBLE_TEXT_LIMIT_RETAINED",
                "text": "",
                "encoding": encoding,
            }
        return {
            "status": "VISIBLE_TEXT_EXTRACTED" if text else "EMPTY_VISIBLE_TEXT_RETAINED",
            "text": text,
            "encoding": encoding,
        }
    except (ValueError, RecursionError):
        return {"status": "STRUCTURE_LIMIT_COMPONENT_RETAINED", "text": "", "encoding": encoding}


def evidence(doc: Any, text: str, start: int, end: int, text_sha: str) -> dict[str, Any]:
    quote = text[start:end]
    return {
        "evidence_id": p.base.stable(
            VERSION, doc.source_id, doc.start, doc.end, start, end, text_sha
        ),
        "source_document_sha256": doc.sha256,
        "derived_text_sha256": text_sha,
        "text_start": start,
        "text_end": end,
        "quote": quote,
        "quote_sha256": p.base.digest(quote.encode()),
        "container_byte_start": doc.start,
        "container_byte_end": doc.end,
        "extractor_version": VERSION,
    }


def disposition(value: str, canonical: str, filer: str) -> dict[str, Any]:
    low = p.lexical(value).lower()
    parsed = p.repurchase(p.lexical(value), canonical, None)
    state, facts = parsed if parsed else ("UNRESOLVED", p.dated_roles(value))
    facts = dict(facts)
    facts.pop("issuer_cik_from_index", None)
    facts.update(
        reporting_filer_cik=filer, issuer_identity_certified=False, public_disclosure_date=None
    )
    strong = bool(
        re.search(
            r"\b(?:board(?: of directors)?|company|we)\b.{0,180}?"
            r"\b(?:approved|approves|authorized|authorised|authorizes|authorises)\b"
            r".{0,240}?\b(?:repurchas\w*|buy[ -]?back)\b",
            low,
        )
    )
    conditional = p.previous.context_reason(value)
    if conditional:
        category, reason = "REJECTION_CONTEXT", conditional
    elif p.modal_or_conditional(
        re.sub(
            r"\b(?:in|during|through|ended)\s+May\b|\bMay\s+(?:19|20)[0-9]{2}\b",
            "<MONTH_CONTEXT>",
            value[: (p.approval_span(value).start() if p.approval_span(value) else len(value))],
            flags=re.I,
        )
    ):
        category, reason = "CONTEXT", "POSSIBLE_FUTURE_OR_CONDITIONAL_ACTION"
    elif re.search(r"\b(?:suspended|terminated|rescinded|expired)\b", low) and not strong:
        category, reason = "CONTEXT", "SUSPENSION_EXPIRATION_OR_TERMINATION"
    elif re.search(r"\b(?:tender offer|accelerated share repurchase|asr agreement)\b", low):
        category, reason = "REJECTION_CONTEXT", "TENDER_OR_ASR_OUTSIDE_INITIAL_OPEN_MARKET_SCOPE"
    elif re.search(
        r"\b(?:preferred|debt|notes|bonds|mortgage|repo agreement)\b", low
    ) and not re.search(r"\bcommon (?:stock|shares?)\b", low):
        category, reason = "REJECTION_CONTEXT", "NON_COMMON_SHARE_OR_NON_EQUITY_WORDING"
    elif (
        re.search(
            r"\b(?:withholding tax|tax withholding|stock options?|"
            r"restricted stock|employee benefit)\b",
            low,
        )
        and not strong
    ):
        category, reason = "REJECTION_CONTEXT", "EMPLOYEE_OR_COMPENSATION_CONTEXT"
    elif state in {"NEW_AUTHORIZATION", "AUTHORIZATION_INCREASE"} and strong:
        category, reason = "CANDIDATE", state
    elif state == "ROUTINE_ACTUAL_REPURCHASES":
        category, reason = "CONTEXT", "EXECUTED_PURCHASES_NOT_NEW_CAPACITY"
    elif re.search(
        r"previously authorized|remaining (?:under|authorization)|unused authorization|"
        r"existing (?:share )?repurchase program",
        low,
    ):
        category, reason = "CONTEXT", "EXISTING_PROGRAM_OR_HISTORICAL_REFERENCE"
    else:
        category, reason = "UNRESOLVED", "REQUIRES_FULL_CONTEXT_ACTION_INTERPRETATION"
    requirements = [
        "INDEPENDENT_FACTUAL_QA",
        "ISSUER_ROLE_AND_SECURITY_ELIGIBILITY",
        "PUBLIC_DISSEMINATION_EVIDENCE",
        "ECONOMIC_ACTION_IDENTITY_AND_DEDUPLICATION",
    ]
    if category == "CANDIDATE":
        if facts.get("sec_class_label") != "COMMON":
            requirements.append("COMMON_SHARE_CLASS_NOT_ESTABLISHED_IN_PASSAGE")
        if not facts.get("open_market_permission"):
            requirements.append("OPEN_MARKET_PERMISSION_NOT_ESTABLISHED_IN_PASSAGE")
        if not facts.get("approval_date"):
            requirements.append("APPROVAL_DATE_NOT_ESTABLISHED_IN_PASSAGE")
        if not facts.get("stated_announcement_date"):
            requirements.append("STATED_ANNOUNCEMENT_DATE_NOT_ESTABLISHED_IN_PASSAGE")
        if len(p.dates(value)) > 1:
            requirements.append("MULTIPLE_DATE_ROLE_REVIEW")
        if not facts.get("authorization_amounts_as_disclosed") and not facts.get(
            "authorization_share_limit_as_disclosed"
        ):
            requirements.append("CAPACITY_NOT_ESTABLISHED_IN_PASSAGE")
        if facts.get("actuals_also_in_passage"):
            requirements.append("AUTHORIZATION_AND_EXECUTION_MIXED_IN_PASSAGE")
    return {
        "category": category,
        "reason": reason,
        "proposed_state": state,
        "facts": facts,
        "unresolved_requirements": requirements,
        "certified_qualifying": False,
        "independent_review_status": "NOT_REVIEWED",
        "public_time_status": "UNRESOLVED",
    }


def scan(doc: Any, text: str, canonical: str, filer: str) -> list[dict[str, Any]]:
    rows = []
    text_sha = p.base.digest(text.encode())
    for block in re.finditer(r"[^\n]+", text):
        for left, right in p.sentence_spans(block[0]):
            value = block[0][left:right]
            if not KEYWORD.search(value):
                continue
            start, end = block.start() + left, block.start() + right
            if len(value) > MAX_PASSAGE:
                details = {
                    "category": "UNRESOLVED",
                    "reason": "OVERSIZED_PASSAGE_RETAINED",
                    "proposed_state": "UNRESOLVED",
                    "facts": {},
                    "unresolved_requirements": ["LONG_PASSAGE_CONTEXT_REVIEW"],
                    "certified_qualifying": False,
                    "independent_review_status": "NOT_REVIEWED",
                    "public_time_status": "UNRESOLVED",
                }
            else:
                details = disposition(value, canonical, filer)
            proof = evidence(doc, text, start, end, text_sha)
            rows.append(
                {
                    "record_id": p.base.stable(
                        VERSION, doc.source_id, doc.start, doc.end, start, end
                    ),
                    "family": "SHARE_REPURCHASE",
                    **details,
                    "evidence": proof,
                }
            )
    return rows


def proposed_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["category"] != "CANDIDATE":
            continue
        # Exact repeated passage within one accession is evidence equivalence only.
        key = p.base.stable(VERSION, row["accession"], row["evidence"]["quote"])
        groups.setdefault(key, []).append(row)
    return [
        {
            "provisional_group_id": key,
            "accession": members[0]["accession"],
            "members": sorted(x["record_id"] for x in members),
            "grouping_basis": "EXACT_PASSAGE_WITHIN_SAME_ACCESSION",
            "economic_action_dedup_certified": False,
            "research_qualifying": False,
            "unresolved_requirements": sorted(
                {x for row in members for x in row["unresolved_requirements"]}
            ),
        }
        for key, members in sorted(groups.items())
    ]
