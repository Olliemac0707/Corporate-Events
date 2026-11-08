"""Outcome-blind SEC container resolution and conservative family-fact proposals."""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any

VERSION = "event-parsers-v1"
STUDY = "corporate-event-alpha-v1"
FAMILIES = (
    "SHARE_REPURCHASE",
    "INSIDER_OPEN_MARKET_PURCHASE",
    "ACTIVIST_13D",
    "DIVIDEND_POSITIVE_CHANGE",
    "SPINOFF_ANNOUNCEMENT",
)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def stable(*parts: object) -> str:
    return digest(json.dumps(parts, separators=(",", ":"), ensure_ascii=True).encode())


def decimal_text(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    try:
        number = Decimal(value.strip().replace(",", ""))
    except InvalidOperation:
        return None
    return format(number, "f") if number.is_finite() else None


def iso_day(value: str | None) -> str | None:
    try:
        return date.fromisoformat(value or "").isoformat()
    except ValueError:
        return None


def cik(value: str | None) -> str | None:
    text = (value or "").strip()
    return text.zfill(10) if re.fullmatch(r"[0-9]{1,10}", text) and int(text) else None


@dataclass(frozen=True)
class Document:
    accession: str
    filename: str
    form: str
    kind: str
    sequence: str
    start: int
    end: int
    raw: bytes

    @property
    def sha256(self) -> str:
        return digest(self.raw)

    @property
    def source_id(self) -> str:
        return stable(self.accession, self.filename, self.sha256)


def resolve_submission(raw: bytes, accession: str) -> tuple[list[Document], dict[str, Any]]:
    if not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession):
        raise ValueError("INVALID_ACCESSION")
    prefix = raw[:65536]
    found = re.search(rb"ACCESSION NUMBER:\s*([0-9-]{20})", prefix, re.I)
    if found and found[1].decode() != accession:
        raise ValueError("CONTAINER_ACCESSION_MISMATCH")
    header = prefix.split(b"<DOCUMENT>", 1)[0]
    acceptance = re.search(rb"<ACCEPTANCE-DATETIME>([^\r\n<]+)", header, re.I)
    filed = re.search(rb"(?:FILED AS OF DATE:|<FILING-DATE>)\s*([0-9]{8})", header, re.I)
    metadata: dict[str, Any] = {
        "acceptance_raw": acceptance[1].decode("ascii", "replace").strip() if acceptance else None,
        "filing_date_raw": filed[1].decode() if filed else None,
        "container_sha256": digest(raw),
        "container_status": "RESOLVED",
        "header_byte_end": len(header),
        "accession_identity_status": "MATCHED" if found else "HEADER_IDENTITY_UNRESOLVED",
    }
    header_text, _ = decode(header)
    for role_name, start_label, stop_label in (
        ("subject", "SUBJECT COMPANY:", "FILED BY:"),
        ("filer", "FILED BY:", "SUBJECT COMPANY:"),
    ):
        role_start = header_text.upper().find(start_label)
        role_block = header_text[role_start:] if role_start >= 0 else ""
        stop = role_block.upper().find(stop_label, len(start_label))
        if stop >= 0:
            role_block = role_block[:stop]
        role_cik = re.search(r"CENTRAL INDEX KEY:\s*([0-9]{1,10})", role_block, re.I)
        role_name_match = re.search(r"COMPANY CONFORMED NAME:\s*([^\r\n]+)", role_block, re.I)
        metadata[role_name + "_cik_raw"] = role_cik[1] if role_cik else None
        metadata[role_name + "_name"] = role_name_match[1].strip() if role_name_match else None
    documents = []
    positions = list(re.finditer(rb"(?im)^<DOCUMENT>\s*\r?$", raw))
    for index, marker in enumerate(positions):
        limit = positions[index + 1].start() if index + 1 < len(positions) else len(raw)
        close = re.search(rb"(?im)^</DOCUMENT>\s*\r?$", raw[marker.end() : limit])
        if not close:
            raise ValueError("UNTERMINATED_DOCUMENT_BLOCK")
        block_end = marker.end() + close.start()
        block = raw[marker.end() : block_end]
        text = re.search(rb"(?im)^<TEXT>[^\S\r\n]*\r?$", block)
        if not text:
            continue
        text_end = re.search(rb"(?im)^</TEXT>[^\S\r\n]*\r?$", block[text.end() :])
        if not text_end:
            raise ValueError("UNTERMINATED_TEXT_BLOCK")

        block_header = block[: text.start()]

        def field(name: bytes, block_header: bytes = block_header) -> str:
            match = re.search(rb"(?im)^<" + name + rb">([^\r\n<]*)", block_header)
            return match[1].decode("utf-8", "replace").strip() if match else ""

        form, filename, sequence = field(b"TYPE"), field(b"FILENAME"), field(b"SEQUENCE")
        if not filename or any(char in filename for char in "/\\?#") or filename in {".", ".."}:
            filename = "UNRESOLVED_FILENAME_" + str(index + 1)
        upper = form.upper()
        kind = (
            "EXHIBIT"
            if upper.startswith("EX-")
            else "OWNERSHIP_XML"
            if upper in {"4", "4/A"}
            else "BENEFICIAL_OWNERSHIP"
            if upper
            in {
                "SC 13D",
                "SCHEDULE 13D",
                "SC 13D/A",
                "SCHEDULE 13D/A",
                "SC 13G",
                "SC 13G/A",
                "SCHEDULE 13G",
                "SCHEDULE 13G/A",
            }
            else "PERIODIC"
            if upper in {"10-K", "10-K/A", "10-Q", "10-Q/A"}
            else "REGISTRATION"
            if upper.startswith(("10-12", "S-1", "S-4"))
            else "PROXY"
            if upper in {"DEF 14A", "DEFM14A", "PRE 14A", "PREM14A"}
            else "BODY"
        )
        start = marker.end() + text.end()
        end = start + text_end.start()
        documents.append(
            Document(accession, filename, form, kind, sequence, start, end, raw[start:end])
        )
    if not documents:
        metadata["container_status"] = "UNSUPPORTED_CONTAINER_RETAINED"
    return documents, metadata


class VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "ix:hidden"}:
            self.hidden += 1
        if not self.hidden and tag in {"p", "div", "br", "tr", "td", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "ix:hidden"} and self.hidden:
            self.hidden -= 1
        if not self.hidden and tag in {"p", "div", "tr", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def decode(raw: bytes) -> tuple[str, str]:
    try:
        return raw.decode("utf-8-sig"), "utf-8-sig"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1-lossless"


def text_document(raw: bytes) -> tuple[str, str]:
    text, encoding = decode(raw)
    parser = VisibleText()
    parser.feed(text)
    return "\n".join(
        line
        for part in "".join(parser.parts).split("\n")
        if (line := re.sub(r"\s+", " ", part).strip())
    ), encoding


def evidence(doc: Document, text: str, start: int, end: int) -> dict[str, Any]:
    return {
        "evidence_id": stable(doc.source_id, VERSION, start, end, digest(text.encode())),
        "source_document_sha256": doc.sha256,
        "extractor_version": VERSION,
        "derived_text_sha256": digest(text.encode()),
        "text_start": start,
        "text_end": end,
        "evidence_sha256": digest(text[start:end].encode()),
        "container_byte_start": doc.start,
        "container_byte_end": doc.end,
        "locator": f"derived-text:{start}:{end}",
    }


def paragraphs(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m[0]) for m in re.finditer(r"[^\n]+", text)]


def proposal(
    family: str,
    state: str,
    anchor: str,
    facts: dict[str, Any],
    proof: dict[str, Any],
    *,
    candidate: bool = True,
) -> dict[str, Any]:
    return {
        "family": family,
        "proposed_state": state,
        "economic_action_anchor": anchor,
        "parser_candidate": candidate,
        "facts": facts,
        "evidence": proof,
        "review_status": "PENDING",
        "disposition": "CANDIDATE" if candidate else "UNRESOLVED",
        "certified_qualifying": False,
        "action_anchor_status": "PROVISIONAL_SOURCE_LOCATOR_REQUIRES_ACTION_REVIEW",
    }


def xml_root(raw: bytes) -> ET.Element:
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", raw, re.I):
        raise ValueError("XML_DTD_OR_ENTITY_FORBIDDEN")
    value = re.sub(rb"(?is)^\s*<XML>\s*", b"", raw)
    value = re.sub(rb"(?is)\s*</XML>\s*$", b"", value)
    return ET.fromstring(value.strip())


def value(root: ET.Element, path: str) -> str | None:
    element = root.find(path)
    if element is None:
        return None
    text = " ".join(element.itertext()).strip()
    return text or None


def ownership(doc: Document) -> dict[str, Any]:
    try:
        root = xml_root(doc.raw)
    except (ET.ParseError, ValueError) as error:
        return {
            "status": "SCHEMA_OR_XML_UNRESOLVED",
            "reason": type(error).__name__,
            "observations": [],
        }
    namespace = root.tag.split("}")[0][1:] if root.tag.startswith("{") else ""
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    if root.tag != "ownershipDocument":
        return {"status": "UNKNOWN_XML_ROOT_RETAINED", "root": root.tag, "observations": []}
    owners = []
    for owner in root.findall("reportingOwner"):
        owners.append(
            {
                "cik_raw": value(owner, "reportingOwnerId/rptOwnerCik"),
                "name": value(owner, "reportingOwnerId/rptOwnerName"),
                "director": value(owner, "reportingOwnerRelationship/isDirector"),
                "officer": value(owner, "reportingOwnerRelationship/isOfficer"),
                "ten_percent_owner": value(owner, "reportingOwnerRelationship/isTenPercentOwner"),
                "other": value(owner, "reportingOwnerRelationship/isOther"),
                "officer_title": value(owner, "reportingOwnerRelationship/officerTitle"),
            }
        )
    footnotes = {
        element.attrib.get("id", ""): " ".join(element.itertext()).strip()
        for element in root.findall("footnotes/footnote")
    }
    transactions = root.findall("nonDerivativeTable/nonDerivativeTransaction") + root.findall(
        "derivativeTable/derivativeTransaction"
    )
    observations = []
    counts = {"nonDerivativeTransaction": 0, "derivativeTransaction": 0}
    for row in transactions:
        counts[row.tag] += 1
        number = counts[row.tag]
        table = "I" if row.tag == "nonDerivativeTransaction" else "II"
        table_name = "nonDerivativeTable" if table == "I" else "derivativeTable"
        path = f"/ownershipDocument/{table_name}/{row.tag}[{number}]"
        linked = sorted({f.attrib.get("id", "") for f in row.iter("footnoteId")})
        scoped = " ".join(footnotes.get(key, "") for key in linked)
        lower = scoped.lower()
        code = value(row, "transactionCoding/transactionCode")
        acquired = value(row, "transactionAmounts/transactionAcquiredDisposedCode/value")
        title = value(row, "securityTitle/value") or ""
        candidate = table == "I" and code == "P" and acquired == "A"
        private = bool(
            re.search(
                (
                    "private (?:purchase|transaction|placement)|priva"
                    "tely negotiated|directly from (?:the )?(?:issuer"
                    "|company)"
                ),
                lower,
            )
        )
        opened = "open market" in lower or "open-market" in lower
        venue = (
            "VENUE_UNRESOLVED"
            if opened == private
            else "OPEN_MARKET_SUPPORTED"
            if opened
            else "PRIVATE_PURCHASE"
        )
        non_plan = bool(
            re.search(
                (
                    "not (?:made |executed )?(?:pursuant to|under|par"
                    "t of).{0,60}(?:10b5|trading plan)"
                ),
                lower,
            )
        )
        positive_plan = re.sub(
            (
                "not (?:made |executed )?(?:pursuant to|under|par"
                "t of).{0,60}(?:10b5[ -]?1|trading plan)"
            ),
            "",
            lower,
        )
        planned = bool(
            re.search(r"(?:pursuant to|under|part of).{0,60}(?:10b5|trading plan)", positive_plan)
        )
        if len(transactions) == 1 and (value(root, "aff10b5One") or "").lower() in {"1", "true"}:
            planned = True
        discretion = (
            "DISCRETION_UNRESOLVED"
            if planned == non_plan
            else "PLAN_TRANSACTION"
            if planned
            else "NON_PLAN_DISCRETION_SUPPORTED"
        )
        ordinary = bool(re.search(r"common (?:stock|shares)|ordinary shares", title, re.I))
        excluded = bool(re.search(r"preferred|depositary|\bads\b|warrant|unit|option", title, re.I))
        instrument = (
            "DERIVATIVE_OR_OTHER"
            if table == "II" or excluded
            else "NONDERIVATIVE_COMMON_SUPPORTED"
            if ordinary
            else "SECURITY_CLASS_UNRESOLVED"
        )
        if doc.form.upper() in {"4/A", "4-A", "FORM 4-A"}:
            state = "AMENDMENT_CORRECTION"
        elif not candidate:
            state = "DERIVATIVE_OR_OTHER" if table == "II" else "REJECTED_NONQUALIFYING"
        elif venue == "PRIVATE_PURCHASE":
            state = "PRIVATE_PURCHASE"
        elif discretion == "PLAN_TRANSACTION":
            state = "PLAN_TRANSACTION"
        elif (
            venue == "OPEN_MARKET_SUPPORTED"
            and discretion == "NON_PLAN_DISCRETION_SUPPORTED"
            and instrument == "NONDERIVATIVE_COMMON_SUPPORTED"
        ):
            state = "OPEN_MARKET_SUPPORTED"
        else:
            state = (
                "DISCRETION_UNRESOLVED"
                if discretion == "DISCRETION_UNRESOLVED"
                else instrument
                if instrument != "NONDERIVATIVE_COMMON_SUPPORTED"
                else "VENUE_UNRESOLVED"
            )
        proof = {
            "evidence_id": stable(doc.source_id, path, VERSION),
            "source_document_sha256": doc.sha256,
            "extractor_version": VERSION,
            "locator": path,
            "evidence_sha256": digest(ET.tostring(row, encoding="utf-8")),
            "container_byte_start": doc.start,
            "container_byte_end": doc.end,
        }
        adoption = re.search(
            r"(?:plan|10b5[ -]?1).{0,50}adopted(?: on)?\s+(\d{4}-\d{2}-\d{2})", scoped, re.I
        )
        facts = {
            "issuer_cik_raw": value(root, "issuer/issuerCik"),
            "issuer_name": value(root, "issuer/issuerName"),
            "documentary_symbol": value(root, "issuer/issuerTradingSymbol"),
            "reporting_owners": owners,
            "security_title": title,
            "table_identifier": table,
            "transaction_code": code,
            "acquired_disposed": acquired,
            "transaction_date": value(row, "transactionDate/value"),
            "deemed_execution_date": value(row, "deemedExecutionDate/value"),
            "shares_decimal": decimal_text(
                value(row, "transactionAmounts/transactionShares/value")
            ),
            "holdings_after_decimal": decimal_text(
                value(row, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")
            ),
            "ownership_form": value(row, "ownershipNature/directOrIndirectOwnership/value"),
            "venue_state": venue,
            "discretion_state": discretion,
            "instrument_state": instrument,
            "footnote_ids": linked,
            "footnote_evidence": {
                key: {
                    "sha256": digest(footnotes.get(key, "").encode()),
                    "locator": f"/ownershipDocument/footnotes/footnote[@id='{key}']",
                }
                for key in linked
            },
            "filing_plan_indicator_raw": value(root, "aff10b5One"),
            "plan_adoption_date": iso_day(adoption[1]) if adoption else None,
            "plan_scope": "SOLE_TRANSACTION"
            if planned and len(transactions) == 1
            else "ROW_FOOTNOTE_OR_UNRESOLVED",
            "transaction_consideration_source_locator": path
            + "/transactionAmounts/transactionPricePerShare",
        }
        observations.append(
            proposal(
                "INSIDER_OPEN_MARKET_PURCHASE",
                state,
                path,
                facts,
                proof,
                candidate=candidate and state != "AMENDMENT_CORRECTION",
            )
        )
    return {
        "status": "STRUCTURAL_PARSE_ONLY_SCHEMA_REVIEW_REQUIRED",
        "schema_version": value(root, "schemaVersion"),
        "namespace": namespace,
        "schema_validation": "NOT_XSD_VALIDATED",
        "reporting_owners": owners,
        "remarks_source_locator": "/ownershipDocument/remarks",
        "observations": observations,
    }


def named_date(text: str, action: str) -> str | None:
    pattern = r"(?:[A-Za-z]{3,9}\.?\s+[0-9]{1,2},?\s+[0-9]{4}|[0-9]{4}-[0-9]{2}-[0-9]{2})"
    match = re.search("(?:" + action + ")" + r"\s*(?:on\s+)?(" + pattern + ")", text, re.I)
    if not match:
        return None
    raw = match[1].replace(".", "")
    direct = iso_day(raw)
    if direct:
        return direct
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def narrative(doc: Document, canonical_form: str, issuer_cik: str | None) -> dict[str, Any]:
    text, encoding = text_document(doc.raw)
    observations: list[dict[str, Any]] = []
    facts: dict[str, Any]
    issuer_routes = canonical_form in {"8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A"}
    registration_routes = canonical_form in {
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
    if canonical_form in {"INITIAL_13D", "AMENDED_13D", "PASSIVE_13G", "AMENDED_13G"}:
        items = list(
            re.finditer(r"\bitem\s*4\s*[.:-]?\s*(?:purpose of (?:the )?transaction)?", text, re.I)
        )
        substantive = []
        for match in items:
            end_match = re.search(r"\bitem\s*5\b", text[match.end() :], re.I)
            stop = match.end() + end_match.start() if end_match else len(text)
            if text[match.end() : stop].strip() and not re.fullmatch(
                r"[\s\d.]+", text[match.end() : stop]
            ):
                substantive.append((match.start(), stop))
        state = "INTENT_UNRESOLVED"
        start, end = substantive[-1] if len(substantive) == 1 else (0, min(len(text), 800))
        item = text[start:end]
        explicit_item = re.sub(
            r"\b(?:may|might|could|reserve\w* the right to)\b[^.\n]{0,500}", "", item, flags=re.I
        )
        affirmative = bool(
            re.search(
                (
                    "\\b(?:intend(?:s)? to|plan(?:s)? to|seek(?:s)? to"
                    "|propos(?:e|es|ed) to|demand(?:s|ed)?|request(?:"
                    "s|ed)?)\\b.{0,100}\\b(?:nominat|replace|elect|boar"
                    "d|directors|strategic review|capital allocation|"
                    "control)"
                ),
                explicit_item,
                re.I,
            )
        )
        reserved = bool(
            re.search(r"\bmay\b.{0,60}\b(?:discuss|nominat|propos|seek|change)", item, re.I)
        )
        nonactivist = bool(
            re.search(
                (
                    "(?:for investment purposes only|not (?:seeking|i"
                    "ntend.{0,15}) to (?:influence|change).{0,30}cont"
                    "rol|no (?:present )?plans or proposals)"
                ),
                item,
                re.I,
            )
        )
        if canonical_form == "INITIAL_13D" and len(substantive) == 1:
            if affirmative and not nonactivist:
                state = "ACTIVIST_INTENT_SUPPORTED"
            elif nonactivist and not affirmative and not reserved:
                state = "NONACTIVIST_INITIAL_13D"
        proof = evidence(doc, text, start, end)
        facts = {
            "subject_issuer_cik": None,
            "filer_cik_from_index": issuer_cik,
            "role_resolution": "SUBJECT_AND_REPORTER_ROLES_REQUIRE_REVIEW",
            "initial_filing_only": canonical_form == "INITIAL_13D",
            "item4_sections_found": len(substantive),
            "reserved_rights_language": reserved,
            "reporting_persons": [],
            "ownership_entries": [],
            "ownership_extraction_status": "UNRESOLVED_REVIEW_ORIGINAL_COVER_PAGES",
            "overlapping_holdings_summed": False,
            "security_class": None,
            "cusip": None,
            "schema_validation": "UNSTRUCTURED_SOURCE_REVIEW_REQUIRED",
        }
        observations.append(
            proposal(
                "ACTIVIST_13D",
                state,
                "item4",
                facts,
                proof,
                candidate=canonical_form == "INITIAL_13D",
            )
        )
    for start, end, paragraph in paragraphs(text):
        low = paragraph.lower()
        proof = evidence(doc, text, start, end)
        if issuer_routes and re.search(
            (
                "(?:repurchas|buy[ -]?back).{0,50}(?:stock|shares"
                ")|(?:stock|share).{0,50}(?:repurchas|buy[ -]?bac"
                "k)"
            ),
            low,
        ):
            negated = bool(
                re.search(
                    (
                        "(?:not|no)\\s+(?:approved|authorized|authorisatio"
                        "n|authorization|new repurchase)"
                    ),
                    low,
                )
            )
            additional = bool(
                re.search(
                    (
                        "(?:increase[ds]?|additional|expand[eds]*).{0,60}"
                        "(?:authorization|authorisation|repurchase|buy[ -"
                        "]?back)|(?:authorization|authorisation).{0,30}in"
                        "creas"
                    ),
                    low,
                )
            )
            fresh = bool(
                re.search(
                    (
                        "(?:board|directors).{0,90}(?:approved|authorized"
                        "|authorised).{0,90}(?:new|repurchase|buyback)|(?"
                        ":new|newly).{0,30}(?:share |stock )?(?:repurchas"
                        "e|buyback) (?:program|authorization)"
                    ),
                    low,
                )
            )
            routine = bool(
                re.search(
                    (
                        "item\\s*703|shares (?:were )?repurchased|repurcha"
                        "sed during|remaining (?:under|authorization)|pre"
                        "viously (?:approved|authorized)"
                    ),
                    low,
                )
            )
            extension = bool(
                re.search(
                    r"extend(?:ed|s|ing)?.{0,60}(?:program|authorization|expiry|expiration)", low
                )
            )
            asr_only = "accelerated share repurchase" in low and not re.search(
                r"(?:new|additional).{0,40}(?:authorization|authorisation|program)", low
            )
            if negated:
                state = "AMBIGUOUS"
            elif asr_only:
                state = "ASR_AGREEMENT"
            elif additional:
                state = "AUTHORIZATION_INCREASE"
            elif fresh and not routine:
                state = "NEW_AUTHORIZATION"
            elif extension:
                state = "EXTENSION_ONLY"
            elif "accelerated share repurchase" in low or re.search(r"\basr\b", low):
                state = "ASR_AGREEMENT"
            elif "tender offer" in low:
                state = "TENDER_OFFER"
            elif re.search(r"program.{0,30}(?:completed|completion)", low):
                state = "COMPLETION"
            elif re.search(r"program.{0,30}(?:expired|expiration)", low):
                state = "EXPIRATION"
            elif routine:
                state = "ROUTINE_ACTUAL_REPURCHASES"
            else:
                state = "AMBIGUOUS"
            monetary = []
            for amount_match in re.finditer(
                r"\$\s*([0-9][0-9,.]*)(?:\s*(million|billion|thousand))?", paragraph, re.I
            ):
                before = paragraph[max(0, amount_match.start() - 70) : amount_match.start()].lower()
                after = paragraph[amount_match.end() : amount_match.end() + 35].lower()
                if re.search(
                    r"per[ -]share|average (?:purchase )?price|price (?:of|was)", before + after
                ):
                    continue
                if (fresh or additional) and (
                    amount_match[2]
                    or re.search(r"up to|additional|authorization|authorisation", before)
                ):
                    monetary.append((amount_match[1], amount_match[2] or ""))
            authorization_amounts = [
                {
                    "decimal": decimal_text(number),
                    "scale": scale.lower() or "units",
                    "currency": "USD"
                    if re.search(r"U\.S\. dollars|US\$|\bUSD\b", paragraph, re.I)
                    else None,
                    "currency_symbol": "$",
                }
                for number, scale in monetary
            ]
            explicit_increment = re.search(
                (
                    "(?:additional|increas(?:e|ed)\\s+(?:the\\s+)?autho"
                    "rization\\s+by)\\s*\\$\\s*([0-9][0-9,.]*)(?:\\s*(mill"
                    "ion|billion|thousand))?"
                ),
                paragraph,
                re.I,
            )
            if explicit_increment and re.search(
                r"per[ -]share",
                paragraph[explicit_increment.end() : explicit_increment.end() + 35],
                re.I,
            ):
                explicit_increment = None
            facts = {
                "issuer_cik_from_index": issuer_cik,
                "authorization_amounts_as_disclosed": authorization_amounts,
                "incremental_amount_as_disclosed": {
                    "decimal": decimal_text(explicit_increment[1]),
                    "scale": explicit_increment[2] or "units",
                }
                if explicit_increment
                else None,
                "incremental_amount_inferred": False,
                "open_market_permission": "open market" in low or "open-market" in low,
                "program_identifier": None,
                "approval_date": named_date(paragraph, r"approved|authorized|authorised"),
                "expiration_date": named_date(
                    paragraph, r"expires|expiration date(?: is)?|through"
                ),
                "prior_authorization": None,
                "remaining_authorization": None,
                "chronology_requires_review": doc.kind == "PERIODIC" or routine,
                "separate_asr_component": "accelerated share repurchase" in low,
                "separate_tender_component": "tender offer" in low,
            }
            observations.append(
                proposal("SHARE_REPURCHASE", state, f"authorization:{start}", facts, proof)
            )
        if issuer_routes and "dividend" in low:
            components = list(
                re.finditer(
                    (
                        "(?:(?:regular|special|quarterly|monthly|annual|s"
                        "emiannual)\\s+)*(?:cash\\s+)?dividend\\b"
                    ),
                    paragraph,
                    re.I,
                )
            )
            for index, match in enumerate(components):
                stop = (
                    components[index + 1].start() if index + 1 < len(components) else len(paragraph)
                )
                clause = paragraph[match.start() : stop]
                if not re.search(
                    r"declar|approv|increas|initiat|special|resum|suspend|decreas|unchanged",
                    paragraph[max(0, match.start() - 100) : stop],
                    re.I,
                ):
                    continue
                special = bool(re.search(r"\bspecial\b", match[0], re.I))
                amounts = re.findall(r"\$\s*([0-9][0-9,.]*)", clause)
                to_amount = re.search(
                    r"\bfrom\s*\$[0-9,.]+\s*(?:per share\s*)?to\s*\$\s*([0-9][0-9,.]*)",
                    clause,
                    re.I,
                )
                amount = (
                    decimal_text(to_amount[1])
                    if to_amount
                    else decimal_text(amounts[0])
                    if len(amounts) == 1
                    else None
                )
                frequencies = {
                    f.lower()
                    for f in re.findall(r"\b(monthly|quarterly|semiannual|annual)\b", clause, re.I)
                }
                frequency = next(iter(frequencies)) if len(frequencies) == 1 else None
                state = (
                    "SPECIAL_DIVIDEND"
                    if special
                    else "RESUMPTION"
                    if "resum" in low
                    else "SUSPENSION"
                    if "suspend" in low
                    else "UNKNOWN_BASELINE"
                )
                facts = {
                    "issuer_cik_from_index": issuer_cik,
                    "declared_cash_per_share_decimal": amount,
                    "amount_candidates_as_disclosed": [decimal_text(a) for a in amounts],
                    "currency": "USD"
                    if amounts and re.search(r"U\.S\. dollars|US\$|\bUSD\b", clause, re.I)
                    else None,
                    "sec_class_label": None,
                    "frequency": frequency,
                    "regular_or_special": "SPECIAL" if special else "REGULAR_REQUIRES_REVIEW",
                    "declaration_date": named_date(paragraph, r"declared"),
                    "record_date": named_date(clause, r"record(?: as of)?"),
                    "ex_date": None,
                    "payment_date": named_date(clause, r"payable|paid"),
                    "baseline_source_ids": [],
                    "baseline_status": "UNKNOWN_BASELINE",
                    "source_reports_increase": "increas" in low,
                    "source_reports_initiation": "initiat" in low,
                    "left_censored_history": not special,
                    "component_index": index + 1,
                }
                observations.append(
                    proposal(
                        "DIVIDEND_POSITIVE_CHANGE",
                        state,
                        f"dividend:{start}:{index + 1}",
                        facts,
                        evidence(doc, text, start + match.start(), start + stop),
                    )
                )
        if (issuer_routes or registration_routes) and re.search(
            (
                "spin[ -]?off|spin off|split[ -]?off|carve[ -]?ou"
                "t|separat.{0,30}(?:business|subsidiary)"
            ),
            low,
        ):
            if re.search(r"cancel(?:led|ed)|abandon(?:ed)?", low):
                state = "CANCELLED_PROPOSAL"
            elif re.search(
                r"completed (?:the |a )?(?:spin|separation)|completion of (?:the |a )?spin", low
            ):
                state = "COMPLETION_ONLY_RECAP"
            elif re.search(r"split[ -]?off", low):
                state = "SPLIT_OFF"
            elif re.search(r"carve[ -]?out|initial public offering|\bipo\b", low) and not re.search(
                r"(?:intend|plan|approved).{0,40}spin", low
            ):
                state = "CARVE_OUT_IPO"
            elif re.search(r"sale of|sell (?:the |a )?(?:business|subsidiary)", low):
                state = "ASSET_SALE"
            elif "strategic alternatives" in low and not re.search(
                r"(?:board.{0,30}approved|intend|plan).{0,40}spin", low
            ):
                state = "STRATEGIC_ALTERNATIVES_REVIEW"
            elif re.search(
                (
                    "(?:intend|plan|board.{0,40}approved|will).{0,80}"
                    "(?:spin|separat)|spin.{0,50}(?:tax-free distribu"
                    "tion|distribution.*shareholders)"
                ),
                low,
            ):
                state = "SPINOFF_INTENTION"
            else:
                state = "AMBIGUOUS"
            facts = {
                "parent_cik_from_index": issuer_cik,
                "spinco_identity": None,
                "business_segment": None,
                "structure_from_sec_locator": proof["locator"],
                "first_disclosure_status": "LEFT_CENSORED_HISTORY",
                "registration_is_first_announcement": False,
                "earliest_source_search_complete": False,
                "future_completion_used": False,
                "future_cancellation_used": False,
            }
            observations.append(
                proposal("SPINOFF_ANNOUNCEMENT", state, f"separation:{start}", facts, proof)
            )
    return {
        "status": "CONSERVATIVE_FACT_PROPOSALS_PENDING_REVIEW",
        "text": text,
        "derived_text_sha256": digest(text.encode()),
        "encoding": encoding,
        "schema_validation": "TEXT_SOURCE_NOT_AN_XML_SCHEMA_CLAIM",
        "observations": observations,
    }


def comparable_dividend(current: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare documentary cash declarations only; never use a price or global factor."""
    result: dict[str, Any] = {
        "state": "UNKNOWN_BASELINE",
        "baseline_source_id": None,
        "reason": "LEFT_CENSORED_HISTORY",
    }
    if current.get("regular_or_special") == "SPECIAL":
        return {
            "state": "SPECIAL_DIVIDEND",
            "baseline_source_id": None,
            "reason": "EXPLICIT_SPECIAL",
        }
    if current.get("context_state") in {"RESUMPTION", "SUSPENSION"}:
        return {
            "state": current["context_state"],
            "baseline_source_id": None,
            "reason": "EXPLICIT_CONTEXT",
        }
    keys = ("issuer_cik", "sec_class_label", "currency", "frequency", "per_share_denominator")
    if any(not current.get(key) for key in keys) or not current.get("public_lower_utc"):
        result["reason"] = "BASELINE_UNRESOLVED"
        return result
    if current.get("regular_or_special") != "REGULAR":
        result["reason"] = "REGULAR_STATUS_UNRESOLVED"
        return result
    cutoff = aware(current["public_lower_utc"])
    eligible = [
        row
        for row in history
        if row.get("regular_or_special") == "REGULAR"
        and all(row.get(key) == current[key] for key in keys)
        and row.get("public_upper_utc")
        and aware(row["public_upper_utc"]) < cutoff
    ]
    if not eligible:
        if (
            current.get("positive_initiation_evidence_id")
            and current.get("prehistory_evidence_ids")
            and current.get("consistent_prehistory_verified") is True
            and Decimal(decimal_text(current.get("declared_cash_per_share_decimal")) or "0") > 0
        ):
            return {
                "state": "DIVIDEND_INITIATION",
                "baseline_source_id": None,
                "reason": "POSITIVE_INITIATION_EVIDENCE_AND_CONSISTENT_KNOWN_PREHISTORY",
            }
        return result
    baseline = max(eligible, key=lambda row: aware(row["public_upper_utc"]))
    left, right = (
        decimal_text(baseline.get("declared_cash_per_share_decimal")),
        decimal_text(current.get("declared_cash_per_share_decimal")),
    )
    if left is None or right is None:
        return result
    state = (
        "DIVIDEND_INCREASE"
        if Decimal(right) > Decimal(left)
        else "DECREASE"
        if Decimal(right) < Decimal(left)
        else "UNCHANGED"
    )
    return {
        "state": state,
        "baseline_source_id": baseline["source_id"],
        "reason": "STRICTLY_PRIOR_COMPARABLE_PUBLIC_REGULAR_DECLARATION",
    }


def aware(text: str) -> datetime:
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.utcoffset() is None:
        raise ValueError("AWARE_TIME_REQUIRED")
    return moment.astimezone(UTC)


def qa_rank(family: str, era: str, form: str, kind: str, accession: str, sha256: str) -> str:
    parts = (STUDY, "phase2-qa-v1", family, era, form, kind, accession, sha256)
    if any("|" in part for part in parts):
        raise ValueError("QA_RANK_DELIMITER_FORBIDDEN")
    return digest("|".join(parts).encode())


def select_qa(rows: list[dict[str, Any]], *, frame_scope: str) -> dict[str, Any]:
    cells: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        if row["role"] == "INDEPENDENT_SOURCE_CONTROL" and not row.get(
            "selected_before_candidate_filter"
        ):
            raise ValueError("CONTROL_WAS_NOT_SELECTED_INDEPENDENTLY")
        key: tuple[str, ...] = (
            row["family"],
            row["era"],
            row["form"],
            row["source_kind"],
            row["role"],
        )
        ranked = {
            **row,
            "rank_sha256": qa_rank(
                row["family"],
                row["era"],
                row["form"],
                row["source_kind"],
                row["accession"],
                row["source_document_sha256"],
            ),
        }
        cells.setdefault(key, []).append(ranked)
    selected, shortfall = [], []
    for key, members in sorted(cells.items()):
        unique = {row["source_id"]: row for row in members}
        ordered = sorted(unique.values(), key=lambda row: (row["rank_sha256"], row["source_id"]))
        selected.extend(ordered[:5])
        shortfall.append(
            {
                "cell": list(key),
                "available": len(ordered),
                "selected": min(5, len(ordered)),
                "shortfall": max(0, 5 - len(ordered)),
            }
        )
    return {
        "frame_scope": frame_scope,
        "quota": 5,
        "selected": selected,
        "cells": shortfall,
        "final_qa_sample": False,
        "full_extraction_gate": "CLOSED_DEVELOPMENT_FRAME_NOT_FINAL_QA",
    }


def cluster_relation(left: dict[str, Any], right: dict[str, Any]) -> str:
    keys = ("issuer_id", "sec_class_label", "economic_action_anchor")
    if any(not left.get(key) or not right.get(key) for key in keys):
        return "AMBIGUOUS"
    if left.get("family") != right.get("family"):
        return "DO_NOT_LINK"
    if (
        all(left[key] == right[key] for key in keys)
        and left.get("explicit_same_action_evidence_id")
        and left.get("explicit_same_action_evidence_id")
        == right.get("explicit_same_action_evidence_id")
    ):
        return "LINK"
    return "AMBIGUOUS"
