"""Contextual SEC development corrections; predecessors and reviews stay immutable."""

from __future__ import annotations

import importlib.util
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "cea_v4_previous", ROOT / "tools/corporate_events/event_parsers_v2.py"
)
assert spec and spec.loader
previous: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = previous
spec.loader.exec_module(previous)
base: Any = previous.base
VERSION = "event-parsers-development-v4.1"
VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "wbr"}
BLOCK_TAGS = {"p", "div", "table", "tr", "td", "th", "li", "h1", "h2", "h3", "h4", "section"}
HIDDEN = {"script", "style", "ix:hidden", "ix:header", "xbrli:context", "xbrli:unit", "head"}
PERIODIC = {"10-K", "10-K/A", "10-Q", "10-Q/A"}
DATE = previous.DATE
MONEY = previous.MONEY
ACTUAL = r"\b(?:we|(?:the )?company)\s+(?:(?:has|had|have)\s+)?(?:repurchased|purchased)\b"
APPROVE = r"\b(?:approved|approves|authorized|authorised|authorizes|authorises)\b"
SUBJECT = r"\b(?:board(?: of directors)?|directors)\b"
INTEREST = r"repurchas|buy[ -]?back|dividend|spin[ -]?off|split[ -]?off|separat"


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    number: int
    children: list[Any] = field(default_factory=list)


class Tree(HTMLParser):
    """Inert DOM with bounded depth; source scripts and external references never execute."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {}, 0)
        self.stack = [self.root]
        self.count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # HTML optional end tags: opening a new peer closes the previous peer.
        close_tags = (
            {"p"}
            if tag in {"p", "div", "table"}
            else {"td", "th"}
            if tag in {"td", "th"}
            else {"tr"}
            if tag == "tr"
            else {"li"}
            if tag == "li"
            else set()
        )
        barriers = (
            {"table", "td", "th", "body"}
            if tag in {"p", "div", "table"}
            else (
                {"table"}
                if tag == "tr"
                else {"tr", "table"}
                if tag in {"td", "th"}
                else {"ul", "ol"}
            )
        )
        if close_tags:
            for i in range(len(self.stack) - 1, 0, -1):
                if self.stack[i].tag in close_tags:
                    del self.stack[i:]
                    break
                if self.stack[i].tag in barriers:
                    break
        self.count += 1
        if self.count > 400_000 or len(self.stack) > 256:
            raise ValueError("STRUCTURAL_LIMIT_RETAIN_SOURCE")
        node = Node(tag, {k: v or "" for k, v in attrs}, self.count)
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, value: str) -> None:
        self.stack[-1].children.append(value)


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def hidden(node: Node) -> str | None:
    identity = " ".join(node.attrs.get(k, "") for k in ("id", "class", "role")).lower()
    if node.tag in HIDDEN or re.search(
        r"display\s*:\s*none|visibility\s*:\s*hidden", node.attrs.get("style", ""), re.I
    ):
        return "HIDDEN_OR_EXECUTABLE_CONTENT"
    if "defref_" in identity or "auth_ref" in identity or "definition" in identity:
        return "TAXONOMY_DEFINITION_SUBTREE"
    return None


def inline(node: Node) -> str:
    if hidden(node):
        return ""
    return "".join(
        value if isinstance(value, str) else " " if value.tag == "br" else inline(value)
        for value in node.children
    )


def lexical(value: str) -> str:
    return value.translate({c: "-" for c in (8208, 8209, 8210, 8211, 8722)})


def genre(doc: Any, opening: str, canonical: str) -> str:
    if doc.form.upper() == "CORRESP":
        return "SUPPORTING_CORRESPONDENCE"
    if canonical in PERIODIC:
        return "PERIODIC_NOTE" if doc.form.upper() == "XML" else "PERIODIC_FILING"
    if doc.form.upper().startswith(("EX-2", "EX-10")) and re.search(
        r"(?:separation and distribution|intellectual property|license|licence).{0,100}agreement",
        opening[:1800],
        re.I,
    ):
        return "SUPPORTING_CONTRACT"
    combined = bool(
        re.search(
            r"proxy statement\s*/\s*prospectus\s*[-/]\s*information statement"
            r"|proxy statement\s*[-/]\s*information statement",
            opening,
            re.I,
        )
    )
    proxies = {"DEF 14A", "DEFM14A", "PRE 14A", "PREM14A"}
    if combined:
        return "PROXY_PROSPECTUS_INFORMATION_STATEMENT"
    if canonical in proxies and (doc.form.upper() in proxies or doc.kind == "PROXY"):
        return (
            "MERGER_PROXY_STATEMENT" if canonical in {"DEFM14A", "PREM14A"} else "PROXY_STATEMENT"
        )
    if re.search(r"information statement|dear.{0,90}(?:stockholder|shareholder)", opening, re.I):
        return "INFORMATION_STATEMENT"
    if re.search(
        r"this.{0,100}agreement|intellectual property.{0,80}agreement", opening, re.I
    ) and re.search(r"whereas|between|recitals", opening, re.I):
        return "SUPPORTING_CONTRACT"
    if re.search(r"news release|press release|announces.{0,80}program", opening, re.I):
        return "PRESS_RELEASE"
    return "EXHIBIT_GENRE_UNRESOLVED" if doc.kind == "EXHIBIT" else "ISSUER_NARRATIVE"


def section_role(text: str, old: str) -> tuple[str, bool]:
    low = re.sub(r"^(?:[a-z]|[0-9]{1,2})[.)]\s*", "", lexical(text).lower())
    if len(text) > 220:
        return old, False
    if re.fullmatch(r"proposed spin[ -]?off(?: of [^.?!]+)?[.]?", low):
        return "NARRATIVE", True
    if re.search(
        (
            "stock (?:price |holder )?performance|performance graph|"
            "selected.{0,12}performance highlights|compensation (?:d"
            "iscussion|decisions)|executive compensation"
        ),
        low,
    ):
        return "PERFORMANCE_OR_COMPENSATION_SECTION", True
    if re.fullmatch(
        r"(?:share repurchases?|share repurchase program|issuer purchases of equity securities|"
        r"quarterly cash dividend|(?:the )?separation and distribution|proposed spin[ -]?off[.]?|"
        r"nature of business|organization and (?:description|basis).*|"
        r"notes to (?:combined|consolidated).*|background.*|reasons for.*|recitals)",
        low,
    ):
        return "NARRATIVE", True
    if re.fullmatch(
        r"(?:corporate transactions|adjustments|antidilution adjustments|employee status)", low
    ):
        return "EMPLOYEE_OR_CONDITIONAL_SECTION", True
    heading = bool(re.fullmatch(r"(?:item\s*\d+\S*.*|note\s*\d+\..*)", text, re.I))
    return ("NARRATIVE", True) if heading else (old, False)


def mapped_text(doc: Any, canonical: str) -> dict[str, Any]:
    original, encoding = base.decode(doc.raw)
    probe = re.sub(r"^\s*<XBRL>\s*", "", original[:8000], flags=re.I)
    for _ in range(12):
        new = re.sub(r"(?s)^\s*(?:<\?.*?\?>|<!--.*?-->|<!DOCTYPE[^>]*>)\s*", "", probe)
        if new == probe:
            break
        probe = new
    structured = bool(
        re.search(r"\.(?:json|xsd|js|css|png|jpg|jpeg|gif|pdf|xls|xlsx|zip)$", doc.filename, re.I)
    ) or bool(
        not re.match(r"\s*<html\b", probe, re.I)
        and re.search(r"<(?:\w+:)?xbrl(?:\s|>)|<schema(?:\s|>)", original[:8000], re.I)
    )
    if structured:
        return {
            "text": "",
            "blocks": [],
            "exclusions": [
                {"reason": "NON_NARRATIVE_COMPONENT_RETAINED", "source_document_sha256": doc.sha256}
            ],
            "encoding": encoding,
            "genre": "UNRESOLVED_STRUCTURED_COMPONENT",
        }
    tree = Tree()
    try:
        if not re.search(r"<[A-Za-z][^>]*>", original):
            buffer: list[str] = []
            paragraphs: list[str] = []
            for line in original.splitlines():
                if not line.strip() or re.match(r"\s*item\s*[0-9]+\b", line, re.I):
                    if buffer:
                        paragraphs.append(" ".join(buffer))
                        buffer.clear()
                    if line.strip():
                        paragraphs.append(line)
                else:
                    buffer.append(line)
            if buffer:
                paragraphs.append(" ".join(buffer))
            for i, value in enumerate(paragraphs, 1):
                tree.root.children.append(Node("p", {}, i, [value]))
        else:
            tree.feed(original)
    except (ValueError, RecursionError):
        return {
            "text": "",
            "blocks": [],
            "exclusions": [
                {"reason": "STRUCTURAL_LIMIT_RETAIN_SOURCE", "source_document_sha256": doc.sha256}
            ],
            "encoding": encoding,
            "genre": "STRUCTURE_UNRESOLVED",
        }
    structural_cache: dict[int, bool] = {}

    def boundary(node: Node) -> bool:
        if node.number not in structural_cache:
            structural_cache[node.number] = node.tag in BLOCK_TAGS | {
                "root",
                "html",
                "body",
                "xbrl",
                "section",
                "article",
            } or any(boundary(c) for c in node.children if isinstance(c, Node))
        return structural_cache[node.number]

    chunks: list[str] = []
    blocks: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    role = "NARRATIVE"
    position = 0

    def emit(text: str, node: Node, table: int | None) -> None:
        nonlocal role, position
        value = norm(text)
        if not value:
            return

        def navigation(n: Node) -> bool:
            return (n.tag == "a" and n.attrs.get("href", "").startswith("#")) or any(
                navigation(c) for c in n.children if isinstance(c, Node)
            )

        if (
            len(value) < 220
            and navigation(node)
            and not re.search(
                r"\b(?:approved|authorized|declared|intends|announced|purchased)\b", value, re.I
            )
        ):
            exclusions.append(
                {
                    "reason": "NAVIGATION_ENTRY_RETAINED",
                    "dom_node": node.number,
                    "source_document_sha256": doc.sha256,
                }
            )
            return
        next_role, heading = section_role(value, role)
        if next_role == "EMPLOYEE_OR_CONDITIONAL_SECTION":
            exclusions.append(
                {"reason": next_role, "dom_node": node.number, "source_document_sha256": doc.sha256}
            )
            return
        role = next_role
        if role != "NARRATIVE":
            exclusions.append(
                {"reason": role, "dom_node": node.number, "source_document_sha256": doc.sha256}
            )
            return
        if (
            table is not None
            and not heading
            and not re.search(
                (
                    "\\b(?:board|directors|we|company|reporting person|on\\s+["
                    "A-Z]|item\\s*4)\\b|\\b(?:approved|authorized|declared)\\b"
                ),
                value,
                re.I,
            )
        ):
            exclusions.append(
                {
                    "reason": "TABLE_CELL_NUMERIC_OR_ROLE_UNRESOLVED",
                    "dom_node": node.number,
                    "table_node": table,
                    "source_document_sha256": doc.sha256,
                }
            )
            return
        if chunks:
            chunks.append("\n")
            position += 1
        start = position
        chunks.append(value)
        position += len(value)
        blocks.append(
            {
                "start": start,
                "end": position,
                "dom_node": node.number,
                "role": "HEADING" if heading else "NARRATIVE",
                "table_node": table,
                "table_numeric_associations": "NOT_INFERRED",
            }
        )

    def visit(node: Node, table: int | None = None) -> None:
        reason = hidden(node)
        if reason:
            exclusions.append(
                {"reason": reason, "dom_node": node.number, "source_document_sha256": doc.sha256}
            )
            return
        if node.tag == "table":
            table = node.number
            exclusions.append(
                {
                    "reason": "TABLE_STRUCTURE_RETAINED_NUMERIC_MAPPING_UNRESOLVED",
                    "dom_node": table,
                    "source_document_sha256": doc.sha256,
                }
            )
        has_blocks = any(isinstance(c, Node) and boundary(c) for c in node.children)
        if not has_blocks:
            emit(inline(node), node, table)
            return
        pending = []
        for child in node.children:
            if isinstance(child, str):
                pending.append(child)
            elif boundary(child):
                emit("".join(pending), node, table)
                pending.clear()
                visit(child, table)
            else:
                pending.append(inline(child))
        emit("".join(pending), node, table)

    try:
        visit(tree.root)
    except RecursionError:
        return {
            "text": "",
            "blocks": [],
            "exclusions": [
                {"reason": "STRUCTURAL_LIMIT_RETAIN_SOURCE", "source_document_sha256": doc.sha256}
            ],
            "encoding": encoding,
            "genre": "STRUCTURE_UNRESOLVED",
        }
    text = "".join(chunks)
    return {
        "text": text,
        "blocks": blocks,
        "exclusions": exclusions,
        "encoding": encoding,
        "genre": genre(doc, text[:5000], canonical),
    }


def evidence(doc: Any, text: str, start: int, end: int) -> dict[str, Any]:
    proof = dict(base.evidence(doc, text, start, end))
    proof["extractor_version"] = VERSION
    proof["evidence_id"] = base.stable(
        doc.source_id, VERSION, start, end, proof["derived_text_sha256"]
    )
    return proof


def dates(value: str) -> list[str]:
    result = []
    for match in re.finditer(DATE, value):
        day = base.named_date("declared " + match[0], "declared")
        if day and day not in result:
            result.append(day)
    return result


def dated_roles(value: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "approval_date": None,
        "stated_announcement_date": None,
        "declaration_date": None,
        "reported_month": None,
    }
    m = re.search(r"\bon\s+(" + DATE + r")\s*,?\s*(.{0,220})", value, re.I)
    if m:
        verbs = re.search(
            r"\b(announced|announces|approved|authorized|authorised|declared|declares)\b",
            m[2],
            re.I,
        )
        if verbs:
            key = (
                "stated_announcement_date"
                if verbs[1].lower() in {"announced", "announces"}
                else "declaration_date"
                if verbs[1].lower() in {"declared", "declares"}
                else "approval_date"
            )
            result[key] = dates(m[1])[0] if dates(m[1]) else None
    result["stated_announcement_date_basis"] = (
        "EXPLICIT_DATED_ANNOUNCEMENT" if result["stated_announcement_date"] else None
    )
    dateline = re.match(
        r"^([A-Za-z ,.'-]{2,90}?)(" + DATE + r")\s*[\u2014\u2013-]\s*(.{0,350})",
        value,
        re.I,
    )
    if (
        not result["stated_announcement_date"]
        and dateline
        and "," in dateline[1]
        and re.search(r"\btoday announced\b", dateline[3], re.I)
        and bool(dates(dateline[2]))
        and len(dates(value)) == 1
        and not re.search(r"approved|authorized|declared", dateline[1], re.I)
    ):
        result["stated_announcement_date"] = dates(dateline[2])[0]
        result["stated_announcement_date_basis"] = "PRINTED_DATELINE_WITH_TODAY_ANNOUNCED"
    month = re.search(r"\bin\s+([A-Za-z]+\s+(?:19|20)[0-9]{2})", value, re.I)
    if month:
        result["reported_month"] = month[1]
    return result


def new_observation(
    family: str,
    state: str,
    facts: dict[str, Any],
    proof: dict[str, Any],
    block: dict[str, Any],
    source_genre: str,
) -> dict[str, Any]:
    row: dict[str, Any] = base.proposal(
        family, state, "v4-source:" + proof["evidence_id"], facts, proof
    )
    row.update(
        {
            "development_parser_version": VERSION,
            "additional_evidence": [],
            "action_anchor_status": "SOURCE_FACT_ONLY_CONTEXTUAL_ACTION_IDENTITY_UNRESOLVED",
            "public_time_status": "TIMESTAMP_UNRESOLVED",
            "structural_context": {**block, "source_genre": source_genre},
            "research_use_status": "WITHHELD_PENDING_INDEPENDENT_FACTUAL_QA",
        }
    )
    return row


def sentence_spans(value: str) -> list[tuple[int, int]]:
    spans = []
    start = 0
    for match in re.finditer(r"(?<=[.!?])\s+(?=[A-Z(])", value):
        left = value[max(0, match.start() - 25) : match.start()]
        if re.search(r"\b(?:Inc|Corp|Co|Ltd|Mr|Mrs|Ms|Dr|Jr|Sr)\.$|(?:[A-Z]\.\s*){1,4}$", left):
            continue
        if value[start : match.start()].strip():
            spans.append((start, match.start()))
        start = match.end()
    if value[start:].strip():
        spans.append((start, len(value)))
    return spans


def modal_or_conditional(value: str) -> bool:
    return bool(
        re.search(
            r"\b(?:if|unless|may|might|could|would|in the future)\b",
            re.sub(DATE, "<DATE>", value, flags=re.I),
            re.I,
        )
    )


def approval_span(value: str) -> re.Match[str] | None:
    for match in re.finditer(APPROVE, value, re.I):
        before = value[max(0, match.start() - 180) : match.start()]
        if re.search(
            r"\b(?:purchased|repurchased|suspended|retired|amount|value of|shares)\b", before, re.I
        ):
            continue
        if re.search(SUBJECT, before, re.I) or re.search(
            r"\b(?:we|company)\s+(?:(?:has|had|have)\s+)?$", before, re.I
        ):
            return match
    return None


def capacity(value: str) -> list[dict[str, Any]]:
    amounts = []
    for m in re.finditer(MONEY, value, re.I):
        near = value[max(0, m.start() - 65) : min(len(value), m.end() + 40)]
        if re.search(r"per (?:common )?share|(?:share )?price|average price", near, re.I):
            continue
        item = {
            "currency_symbol": "$",
            "decimal": base.decimal_text(m[1]),
            "scale": m[2].lower() if m[2] else None,
            "currency": None,
        }
        if item not in amounts:
            amounts.append(item)
    return amounts


def repurchase(value: str, canonical: str, issuer: str | None) -> tuple[str, dict[str, Any]] | None:
    low = value.lower()
    if not re.search(previous.REP, low) or not re.search(r"shares?|stock|program", low):
        return None
    if re.search(r"\b(?:suspended|retired)\b|accounted for|we record|excludes|definition", low):
        return None
    action = approval_span(value)
    operative = action or re.search(ACTUAL, value, re.I)
    prefix = value[: operative.start()] if operative else value
    if modal_or_conditional(prefix) or re.search(r"\b(?:not|never)\b", prefix, re.I):
        return None
    actual = bool(
        re.search(ACTUAL + r".{0,100}(?:common stock|shares?|stock)", value, re.I)
        or re.search(r"\bshares (?:were|have been) repurchased\b", low)
    )
    asr = "accelerated share repurchase" in low and bool(re.search(r"entered|executed", low))
    tender = "tender offer" in low and bool(re.search(r"announced|commenced|launched", low))
    extension = bool(re.search(SUBJECT + r".{0,80}\bextended\b", low))
    expired = bool(re.search(r"program (?:has |had )?expired", low)) and not re.search(
        r"no expiration", low
    )
    if not any((action, actual, asr, tender, extension, expired)):
        return None
    if action and re.search(
        r"recommendation|discussions?|possibility|explor|consider", value[action.end() :], re.I
    ):
        return None
    renewal = bool(re.search(r"\brenew(?:al|ed)\b", low))
    extra = bool(re.search(r"\badditional\b|\bincreased\b|\bexpanded\b", low))
    state = (
        "ASR_AGREEMENT"
        if asr
        else "TENDER_OFFER"
        if tender
        else "EXTENSION_ONLY"
        if extension
        else "EXPIRATION"
        if expired
        else "AMBIGUOUS"
        if action and renewal
        else "AUTHORIZATION_INCREASE"
        if action and extra
        else "NEW_AUTHORIZATION"
        if action
        else "ROUTINE_ACTUAL_REPURCHASES"
    )
    another = bool(action and not extra and re.search(r"\banother\s+(?:share\s+)?repurchase", low))
    if another and state == "NEW_AUTHORIZATION":
        state = "AMBIGUOUS"
    approved_text = value[action.end() :] if action else ""
    execution = re.search(ACTUAL, approved_text, re.I)
    if execution:
        approved_text = approved_text[: execution.start()]
    amounts = capacity(approved_text) if action else []
    share_limit = re.search(r"up to\s+([0-9,]+)\s+shares", approved_text, re.I)
    facts = {
        **dated_roles(value),
        "issuer_cik_from_index": issuer,
        "authorization_amounts_as_disclosed": amounts,
        "authorization_share_limit_as_disclosed": base.decimal_text(share_limit[1])
        if share_limit
        else None,
        "incremental_amount_as_disclosed": amounts[0]
        if extra and not renewal and len(amounts) == 1
        else None,
        "incremental_amount_inferred": False,
        "renewal_requires_capacity_review": renewal,
        "contextual_subtype_requires_review": another,
        "authorization_wording_context": "ANOTHER_REPURCHASE_PROGRAM_RELATIONSHIP_UNRESOLVED"
        if another
        else None,
        "authorization_vs_actuals": "AUTHORIZATION"
        if action
        else "ACTUAL_EXECUTION_RECAP"
        if actual
        else "CONTEXT_ACTION",
        "sec_class_label": "COMMON" if re.search(r"common (?:stock|shares?)", low) else None,
        "actuals_also_in_passage": actual,
        "open_market_permission": bool(re.search(r"open[ -]market", low)),
        "expiration_date": None,
        "chronology_requires_review": True,
        "parent_periodic_filing": canonical in PERIODIC,
        "capacity_and_action_identity_require_review": True,
        "public_disclosure_date": None,
    }
    reported = [
        facts.get(k) for k in ("approval_date", "stated_announcement_date", "reported_month")
    ]
    years = [
        int(m[0])
        for value in reported
        if value
        for m in re.finditer(r"\b(?:19|20)[0-9]{2}\b", value)
    ]
    facts["action_year_eligibility"] = (
        "DISCLOSED_YEAR_PRE_STUDY_CONTEXT"
        if years and max(years) < 2006
        else "REQUIRES_INFORMATION_CLOCK_REVIEW"
    )
    return state, facts


def payment_date_roles(value: str) -> tuple[list[str], list[str]]:
    scheduled: list[str] = []
    paid: list[str] = []
    markers = list(re.finditer(r"\b(?:payable|paid)\b", value, re.I))
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(value)
        prefix = value[max(0, marker.start() - 40) : marker.start()]
        if re.search(r"\b(?:not|never)\s+(?:been\s+)?$", prefix, re.I):
            continue
        clause = value[marker.end() : end]
        clause = re.split(
            r"\b(?:to|for)\s+(?:common )?(?:shareholders|stockholders)", clause, flags=re.I
        )[0]
        target = scheduled if marker.group().lower() == "payable" else paid
        target.extend(day for day in dates(clause) if day not in target)
    return scheduled, paid


def dividend(value: str, issuer: str | None) -> tuple[str, dict[str, Any]] | None:
    low = value.lower()
    if not re.search(r"\bdividends?\b", low):
        return None
    if re.search(
        (
            "never|do not|does not|not currently|if declared|preferr"
            "ed|preference|dividend equivalents|dividend income"
        ),
        low,
    ):
        return None
    trigger = re.search(
        (
            "\\b(?:declared|declares|approved)\\s+(?:(?:a|an|the|its|r"
            "egular|quarterly|monthly|special|cash|annual|semiannual"
            "|increased)\\s+)*dividends?\\b"
        ),
        low,
    )
    if not trigger or not re.search(r"\b(?:board|directors|company|we)\b", low[: trigger.start()]):
        return None
    clause = value[trigger.start() :]
    amounts = list(re.finditer(MONEY, clause, re.I))
    one_amount = base.decimal_text(amounts[0][1]) if len(amounts) == 1 else None
    per_share = bool(re.search(r"per (?:common )?share", low))
    common = bool(re.search(r"common (?:stock|shares?)|per common share", low))
    record_match = re.search(r"(?:shareholders|stockholders) of record(?:[^.;]*?)", value, re.I)
    record_part = value[record_match.start() :] if record_match else ""
    record_part = re.split(
        r"(?:and\s+)?(?:the\s+)?company\s+paid|\bwe\s+paid", record_part, flags=re.I
    )[0]
    records = dates(record_part)
    scheduled, paid = payment_date_roles(value)
    payments = list(dict.fromkeys([*scheduled, *paid]))
    multiple = len(records) > 1 or len(payments) > 1
    frequency = re.findall(r"\b(monthly|quarterly|annual|semiannual)\b", low)
    special = bool(re.search(r"\bspecial (?:cash )?dividend", low))
    return "SPECIAL_DIVIDEND" if special else "UNKNOWN_BASELINE", {
        **dated_roles(value),
        "issuer_cik_from_index": issuer,
        "declared_cash_per_share_decimal": one_amount if per_share else None,
        "sec_class_label": "COMMON" if common else None,
        "currency": "USD" if re.search(r"US\$|U\.S\. dollars|\bUSD\b", value, re.I) else None,
        "frequency": frequency[0] if frequency and len(set(frequency)) == 1 else None,
        "regular_or_special": "SPECIAL"
        if special
        else "REGULAR"
        if frequency
        else "REGULAR_REQUIRES_REVIEW",
        "record_date": records[0] if len(records) == 1 and not multiple else None,
        "payment_date": payments[0] if len(payments) == 1 and not multiple else None,
        "record_dates_as_disclosed": records,
        "payment_dates_as_disclosed": payments,
        "scheduled_payment_dates_as_disclosed": scheduled,
        "reported_paid_dates_as_disclosed": paid,
        "payment_date_role_status": "MULTIPLE_PAYMENT_DATE_ROLES_REVIEW"
        if scheduled and paid
        else "SCHEDULED_AS_DISCLOSED"
        if scheduled
        else "REPORTED_PAID_AS_DISCLOSED"
        if paid
        else "UNRESOLVED",
        "declaration_identity_status": "MULTIPLE_DISTRIBUTIONS_REVIEW"
        if multiple
        else "SINGLE_DECLARATION_REQUIRES_REVIEW",
        "baseline_status": "UNKNOWN_BASELINE",
        "baseline_source_ids": [],
        "source_reports_increase": bool(
            re.search(r"\bincreased\s+(?:its\s+)?(?:regular |quarterly |cash )*dividend", low)
        ),
        "public_disclosure_date": None,
        "single_action_certified": False,
    }


def nonoperative_spin_reason(value: str) -> str | None:
    low = re.sub(r"^(?:[a-z]|[0-9]{1,2})[.)]\s*", "", lexical(value).lower()).strip()
    actor = (
        r"^(?:on\s+" + DATE + r",?\s*)?"
        r"(?:(?:the )?board(?: of directors)?|(?:the )?company|we)\s+"
        r"(?:(?:has|have|had)\s+)?"
    )
    action = (
        r"(?:(?:announced|announces)\s+(?:a |the |its )?(?:plan|intention)\s+to\s+"
        r"(?:spin[ -]?off|separate)|(?:approved|approves)\s+(?:a |the |its )?"
        r"(?:plan to |planned )?(?:spin[ -]?off|separation)|"
        r"(?:plans|intends)\s+to\s+(?:spin[ -]?off|separate))\b"
    )
    operative = bool(re.match(actor + action, low, re.I))
    if re.fullmatch(r"(?:proposed )?spin[ -]?off(?: of [^.?!]{1,180})?[.]?", low) and not re.search(
        r"\b(?:approved|announced|intends|will|entered|agreed)\b", low
    ):
        return "HEADING_ONLY_CONTEXT"
    if re.match(r"(?:for more information|for additional information|see\b)", low):
        return "CROSS_REFERENCE_CONTEXT"
    if not operative and re.search(
        r"(?:computation|calculation) of funds from operations"
        r"|funds from operations.{0,100}(?:computed|determined|adjusted|means)",
        low,
    ):
        return "FINANCIAL_DEFINITION_CONTEXT"
    if (
        not operative
        and re.search(
            r"\b(?:business|division|segment)\b.{0,100}\bthat\b.{0,80}\bagreed to spin", low
        )
        and re.search(
            r"\b(?:generated|had|reported)\s+(?:net |total )?(?:sales|assets|revenues)", low
        )
    ):
        return "FINANCIAL_DESCRIPTION_CONTEXT"
    return None


def spin(
    value: str, issuer: str | None, source_genre: str, proof: dict[str, Any]
) -> tuple[str, dict[str, Any]] | None:
    low = lexical(value).lower()
    if nonoperative_spin_reason(value):
        return None
    if not re.search(
        previous.SPIN
        + r"|sponsored spin|\bseparat\w*\b.{0,180}\b(?:business|companies|publicly.traded)",
        low,
    ):
        return None
    if re.search(
        r"\bif\b.{0,1600}(?:adjust|awards?|options?|espp)|antidilution|anti-dilution", low
    ):
        return None
    if re.search(
        (
            "strategic alternatives|possibility of|possible.{0,35}(?"
            ":spin|transaction)|pursue discussions|proposed a possib"
            "le"
        ),
        low,
    ):
        state = "STRATEGIC_ALTERNATIVES_REVIEW"
    elif re.search(
        r"(?:board|directors).{0,180}(?:approved|determined).{0,120}"
        r"(?:plan|separat).{0,180}(?:business|companies|publicly.traded)",
        low,
    ):
        state = "SPINOFF_INTENTION"
    else:
        proposal = previous.spin(value, proof, issuer, "BODY")
        if not proposal:
            return None
        state = proposal["proposed_state"]
    return state, {
        "filer_cik_from_index": issuer,
        "parent_cik_from_index": None,
        "parent_role_status": "TRANSACTION_PARENT_REQUIRES_DOCUMENTARY_IDENTITY",
        "source_component_role": source_genre,
        "pro_rata_distribution_stated": bool(re.search(r"pro[ -]rata", low)),
        "first_disclosure_status": "LEFT_CENSORED_HISTORY",
        "registration_is_first_announcement": False,
        "earliest_source_search_complete": False,
        "recaps_prior_announcement": bool(
            re.search(r"\bon\s+" + DATE + r".{0,350}\bannounced\b", value, re.I)
            or re.search(r"\b(?:as )?previously announced\b|\bearlier announcement\b", value, re.I)
        ),
        "stated_announcement_date": dated_roles(value)["stated_announcement_date"],
        "future_completion_used": False,
        "future_cancellation_used": False,
        "public_disclosure_date": None,
    }


def consolidate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Identical wording at different locations is never an economic-action identity.
    seen = set()
    output = []
    for row in rows:
        key = (row["family"], row["evidence"]["evidence_id"], row["proposed_state"])
        if key not in seen:
            seen.add(key)
            output.append(row)
    return output


def attach_program_support(
    doc: Any,
    text: str,
    value: str,
    start: int,
    sentences: list[tuple[int, int]],
    index: int,
    row: dict[str, Any],
) -> None:
    facts = row["facts"]
    if row["family"] != "SHARE_REPURCHASE" or facts["authorization_vs_actuals"] != "AUTHORIZATION":
        return
    facts["supporting_program_facts"] = []
    facts["supporting_field_evidence"] = {}
    facts["adjacent_program_fact_conflict"] = False
    for left, right in sentences[index + 1 : index + 4]:
        adjacent = value[left:right]
        if len(adjacent) > 700:
            break
        if re.search(r"^There is no requirement that we repurchase", adjacent, re.I):
            continue
        limit = re.match(
            r"The maximum total value of (?:the|this) repurchase is "
            r"(?:not to exceed|limited to|up to)\s*",
            adjacent,
            re.I,
        )
        reference = re.match(
            r"Under (?:the |this )?(?:new )?(?:share )?repurchase program\b", adjacent, re.I
        )
        if not limit and not reference:
            break
        if re.search(r"preferred|different program|other program|suspend|expired", adjacent, re.I):
            break
        amounts = capacity(adjacent[limit.end() :]) if limit else capacity(adjacent)
        common = bool(re.search(r"common (?:stock|shares?)", adjacent, re.I))
        if not amounts and not common:
            break
        proof = evidence(doc, text, start + left, start + right)
        row["additional_evidence"].append(proof)
        facts["supporting_program_facts"].append(
            {
                "role": "TOTAL_VALUE_CAP" if limit else "EXPLICIT_PROGRAM_REFERENCE",
                "amounts": amounts,
                "common_class_stated": common,
                "evidence_id": proof["evidence_id"],
            }
        )
        if amounts:
            existing = facts["authorization_amounts_as_disclosed"]
            if not existing:
                facts["authorization_amounts_as_disclosed"] = amounts
                facts["supporting_field_evidence"]["authorization_amounts_as_disclosed"] = [
                    proof["evidence_id"]
                ]
            elif existing != amounts:
                facts["adjacent_program_fact_conflict"] = True
        if common and facts["sec_class_label"] is None:
            facts["sec_class_label"] = "COMMON"
            facts["supporting_field_evidence"]["sec_class_label"] = [proof["evidence_id"]]


def narrative(
    doc: Any, canonical_form: str, issuer_cik: str | None, *, roles: dict[str, Any] | None = None
) -> dict[str, Any]:
    mapped = mapped_text(doc, canonical_form)
    text = mapped["text"]
    observations: list[dict[str, Any]] = []
    contexts = list(mapped["exclusions"])
    if canonical_form in previous.BENEFICIAL and text:
        old, excluded = previous.activist(doc, text, canonical_form, issuer_cik, roles or {})
        contexts.extend(excluded)
        for row in old:
            proof = row["evidence"]
            row["evidence"] = evidence(doc, text, proof["text_start"], proof["text_end"])
            item = text[proof["text_start"] : proof["text_end"]]
            row["facts"]["investment_or_no_present_plan_language"] = bool(
                re.search(
                    r"investment purposes only|no (?:reporting person|present plans|present plan)"
                    r"|none.{0,80}(?:present plan|present proposal)",
                    norm(item),
                    re.I,
                )
            )
            row["development_parser_version"] = VERSION
            row["economic_action_anchor"] = "v4-source:" + row["evidence"]["evidence_id"]
            row["additional_evidence"] = []
            row["public_time_status"] = "TIMESTAMP_UNRESOLVED"
            row["research_use_status"] = "WITHHELD_PENDING_INDEPENDENT_FACTUAL_QA"
            observations.append(row)
    else:
        for block in mapped["blocks"]:
            start, end = block["start"], block["end"]
            value = text[start:end]
            if block["role"] != "NARRATIVE" or not re.search(
                INTEREST + r"|sponsored spin", value, re.I
            ):
                continue
            sentences = sentence_spans(value)
            for index, (left, right) in enumerate(sentences):
                sentence = value[left:right]
                if re.search(
                    r"\b(?:declared|declares|approved)\b.{0,100}dividend", sentence, re.I
                ) and index + 1 < len(sentences):
                    nxt = value[sentences[index + 1][0] : sentences[index + 1][1]]
                    if re.match(r"(?:the|this) dividend\b", nxt, re.I) and re.search(
                        r"payable|record", nxt, re.I
                    ):
                        right = sentences[index + 1][1]
                        sentence = value[left:right]
                if len(sentence) > 6000:
                    contexts.append(
                        {
                            "reason": "OVERSIZED_NARRATIVE_RETAINED",
                            "evidence": evidence(doc, text, start + left, start + right),
                        }
                    )
                    continue
                if previous.context_reason(sentence):
                    contexts.append(
                        {
                            "reason": "CONDITIONAL_OR_NON_EVENT_CONTEXT",
                            "evidence": evidence(doc, text, start + left, start + right),
                        }
                    )
                    continue
                proof = evidence(doc, text, start + left, start + right)
                candidates = []
                if canonical_form in previous.ISSUER_FORMS:
                    candidates.extend(
                        [
                            ("SHARE_REPURCHASE", repurchase(sentence, canonical_form, issuer_cik)),
                            ("DIVIDEND_POSITIVE_CHANGE", dividend(sentence, issuer_cik)),
                        ]
                    )
                if canonical_form in previous.SPIN_FORMS:
                    candidates.append(
                        ("SPINOFF_ANNOUNCEMENT", spin(sentence, issuer_cik, mapped["genre"], proof))
                    )
                found = False
                for family, parsed in candidates:
                    if parsed:
                        state, facts = parsed
                        proof = evidence(doc, text, start + left, start + right)
                        row = new_observation(family, state, facts, proof, block, mapped["genre"])
                        attach_program_support(doc, text, value, start, sentences, index, row)
                        observations.append(row)
                        found = True
                if not found:
                    contexts.append(
                        {
                            "reason": nonoperative_spin_reason(sentence)
                            or "NO_OPERATIVE_ACTION_ESTABLISHED",
                            "evidence": evidence(doc, text, start + left, start + right),
                        }
                    )
    return {
        "status": "V4_CONTEXTUAL_FACTS_PENDING_INDEPENDENT_REVIEW",
        "parser_version": VERSION,
        "text": text,
        "derived_text_sha256": base.digest(text.encode()),
        "encoding": mapped["encoding"],
        "source_genre": mapped["genre"],
        "structural_blocks": mapped["blocks"],
        "observations": consolidate(observations),
        "retained_contexts": contexts,
        "context_reason_counts": dict(Counter(x["reason"] for x in contexts)),
        "coverage_status": "STRUCTURAL_EXTRACTION_NOT_WHOLE_SOURCE_NEGATIVE",
        "schema_validation": "NO_NEW_HISTORICAL_SCHEMA_AUTHORITY",
    }


def ownership(doc: Any) -> dict[str, Any]:
    return dict(previous.ownership(doc))


def documentary_links(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[str]] = {}
    for row in rows:
        facts = row["facts"]
        if row["family"] == "SHARE_REPURCHASE":
            day = facts.get("approval_date") or facts.get("stated_announcement_date")
            disclosed = facts.get("authorization_amounts_as_disclosed")
            scales = {None: 1, "thousand": 1000, "million": 1000000, "billion": 1000000000}
            values = (
                [
                    [
                        a.get("currency"),
                        a.get("currency_symbol"),
                        format(Decimal(a["decimal"]) * scales[a.get("scale")], "f"),
                    ]
                    for a in disclosed
                ]
                if disclosed and all(a.get("scale") in scales for a in disclosed)
                else disclosed
            )
            valid = day and values and len(values) == 1
        elif row["family"] == "DIVIDEND_POSITIVE_CHANGE":
            day = facts.get("declaration_date")
            values = facts.get("declared_cash_per_share_decimal")
            valid = (
                day
                and values
                and facts.get("declaration_identity_status") != "MULTIPLE_DISTRIBUTIONS_REVIEW"
            )
        else:
            continue
        if not valid or not facts.get("sec_class_label") or not facts.get("issuer_cik_from_index"):
            continue
        key = base.stable(
            row["accession"],
            row["family"],
            facts["issuer_cik_from_index"],
            day,
            values,
            facts["sec_class_label"],
        )
        groups.setdefault(key, []).append(row["development_observation_id"])
    return [
        {
            "link_id": key,
            "members": sorted(set(value)),
            "status": "DOCUMENTARY_SUBTYPE_DISAGREEMENT_REVIEW"
            if len({r["proposed_state"] for r in rows if r["development_observation_id"] in value})
            > 1
            else "DOCUMENTARY_MATCH_SUGGESTION_NOT_FINAL_ACTION_MERGE",
            "proposed_states": sorted(
                {r["proposed_state"] for r in rows if r["development_observation_id"] in value}
            ),
            "final_dedup_certified": False,
        }
        for key, value in sorted(groups.items())
        if len(set(value)) > 1
    ]
