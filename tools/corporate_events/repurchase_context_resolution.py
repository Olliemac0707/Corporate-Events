"""Reconcile the frozen shortlist using explicitly cited source-context proposals."""

from __future__ import annotations

import argparse
import calendar
import importlib.util
import json
import re
import sys
from collections import Counter
from decimal import Decimal
from html import escape
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "protocols/corporate-event-alpha-v1"
PRIVATE = ROOT / "reports/corporate-event-alpha-v1/phase2-private"
OUTPUT = PRIVATE / "repurchase-context-resolution-v1"
INPUT = PRIVATE / "repurchase-context-input-v1.json"
LEDGER_SHA = "e16e3c77d10d87a1c6978d1e188bc624d489b30477b22acde68e1bbc09413c18"
REGISTERED = {
    "tools/corporate_events/repurchase_context_resolution.py",
    "tests/test_repurchase_context_resolution.py",
    "protocols/corporate-event-alpha-v1/repurchase-context-policy-v1.json",
}
spec = importlib.util.spec_from_file_location(
    "context_source_ledger", ROOT / "tools/corporate_events/bounded_repurchase_ledger_v3.py"
)
assert spec and spec.loader
ledger: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ledger
spec.loader.exec_module(ledger)
sec = ledger.sec


def amount_is_stated(value: str, unit: str, text: str) -> bool:
    target = Decimal(value)
    multipliers = {
        None: Decimal(1),
        "thousand": Decimal(1000),
        "million": Decimal(1000000),
        "billion": Decimal(1000000000),
    }
    pattern = (
        r"(?P<symbol>\$)?\s*(?P<number>[0-9][0-9,.]*|"
        r"one|two|three|four|five|six|seven|eight|nine|ten)\s*"
        r"(?P<scale>thousand|million|billion)?\s*(?P<suffix>%|percent|shares|dollars)?"
    )
    words = dict(
        zip(
            ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"),
            range(1, 11),
            strict=True,
        )
    )
    for match in re.finditer(pattern, text, re.I):
        number = match["number"].lower()
        parsed = Decimal(words[number]) if number in words else Decimal(number.replace(",", ""))
        parsed *= multipliers[(match["scale"] or "").lower() or None]
        suffix = (match["suffix"] or "").lower()
        observed = (
            "DOLLARS"
            if match["symbol"] or suffix == "dollars"
            else "SHARES"
            if suffix == "shares"
            else "PERCENT_OUTSTANDING_SHARES"
            if suffix in {"%", "percent"}
            else None
        )
        if parsed == target and observed == unit:
            return True
    return False


def validate_case(
    case: dict[str, Any], records: dict[str, Any], evidence: dict[str, Any]
) -> dict[str, Any]:
    members = [records[key] for key in case["members"]]
    if any(row["accession"] != case["accession"] for row in members):
        raise ValueError("CASE_MEMBERS_CROSS_ACCESSIONS")
    support = case["support"]
    if any(key not in evidence for ids in support.values() for key in ids):
        raise ValueError("CASE_SUPPORT_EVIDENCE_MISSING")
    used = {key for ids in support.values() for key in ids}
    if not used or any(evidence[key]["accession"] != case["accession"] for key in used):
        raise ValueError("CASE_SOURCE_CONTEXT_OUTSIDE_ACCESSION")
    if len(members) > 1 and (
        case["grouping_basis"] != "EXPLICIT_DOCUMENT_LINK_AND_PROGRAMME_REFERENCE"
        or not support.get("document_link")
        or not support.get("programme_identity")
    ):
        raise ValueError("MERGE_REQUIRES_DOCUMENTARY_LINK_NOT_AMOUNT_DATE_SIMILARITY")
    if not members and (
        not case.get("related_to") or case["grouping_basis"] != "RELATED_CONTEXT_DISTINCT_ACTION"
    ):
        raise ValueError("EXTRA_ACTION_REQUIRES_EXPLICIT_RELATED_CONTEXT")

    def quotes(field: str) -> str:
        return "\n".join(evidence[key]["quote"] for key in support.get(field, []))

    if len(members) > 1 and not re.search(
        r"exhibit\s+99|press release|news release", quotes("document_link"), re.I
    ):
        raise ValueError("DOCUMENTARY_LINK_WORDING_NOT_CITED")

    facts = case["facts"]
    for field in ("stated_announcement_date", "approval_date", "effective_date"):
        value = facts.get(field)
        if value is None:
            continue
        text = quotes(field)
        if value not in ledger.rules.p.dates(text):
            raise ValueError("DATE_NOT_PRESENT_IN_CITED_FIELD_EVIDENCE")
        if field == "approval_date":
            basis = facts.get("approval_date_basis")
            if basis == "DATELINE_TODAY_AUTHORIZED":
                if not re.search(r"today\s+authori[sz]ed", text, re.I):
                    raise ValueError("APPROVAL_DATE_ROLE_NOT_ESTABLISHED")
            elif (
                basis != "DIRECT_APPROVAL_DATE"
                or re.search(r"announced|issued a press release", text, re.I)
                or not re.search(r"approved|authori[sz]ed", text, re.I)
            ):
                raise ValueError("ANNOUNCEMENT_DATE_CANNOT_BECOME_APPROVAL_DATE")
        if field == "stated_announcement_date" and not re.search(
            r"announced|issued|press release|news release", text, re.I
        ):
            raise ValueError("STATED_ANNOUNCEMENT_DATE_ROLE_NOT_ESTABLISHED")
        if field == "effective_date" and not re.search(r"effective", text, re.I):
            raise ValueError("EFFECTIVE_DATE_ROLE_NOT_ESTABLISHED")
    for field in ("approval_month", "stated_announcement_month"):
        if facts.get(field):
            month_values = set()
            month_names = {
                name.lower(): index for index, name in enumerate(calendar.month_name) if name
            }
            for match in re.finditer(r"\b([A-Za-z]+)\s+((?:19|20)[0-9]{2})\b", quotes(field)):
                if match[1].lower() in month_names:
                    month_values.add(f"{match[2]}-{month_names[match[1].lower()]:02d}")
            if facts[field] not in month_values:
                raise ValueError("PARTIAL_MONTH_DATE_NOT_CITED")
    if facts["common_share_scope"] == "EXPLICIT_COMMON" and not re.search(
        r"common\s+(?:stock|shares)", quotes("common_share_scope"), re.I
    ):
        raise ValueError("COMMON_SHARE_SCOPE_NOT_CITED")
    if facts["open_market_permission"] == "EXPLICIT" and not re.search(
        r"open[ -]market", quotes("open_market_permission"), re.I
    ):
        raise ValueError("OPEN_MARKET_PERMISSION_NOT_CITED")
    for capacity in case["capacities"]:
        if any(
            key not in used or evidence[key]["accession"] != case["accession"]
            for key in capacity["evidence"]
        ):
            raise ValueError("CAPACITY_EVIDENCE_OUTSIDE_CASE_SUPPORT")
        text = "\n".join(evidence[key]["quote"] for key in capacity["evidence"])
        if capacity.get("derived") is not False or not amount_is_stated(
            capacity["value_decimal"], capacity["unit"], text
        ):
            raise ValueError("CAPACITY_MUST_BE_STATED_NOT_DERIVED")
        if capacity.get("iso_currency") is not None:
            raise ValueError("NO_CURRENCY_CODE_INFERENCE_IN_THIS_RESOLUTION")
    if case["temporal_scope"] == "NON_TARGET_ASR":
        if not re.search(
            r"accelerated (?:stock|share) repurchase|use of derivatives",
            quotes("action_identity"),
            re.I,
        ):
            raise ValueError("ASR_CONTEXT_NOT_CITED")
        status = "NON_TARGET_ASR_EXECUTION_CONTEXT"
    elif case["temporal_scope"] == "EXPLICIT_EARLIER_ANNOUNCEMENT":
        if not re.search(r"announced", quotes("temporal_scope"), re.I):
            raise ValueError("EARLIER_ANNOUNCEMENT_NOT_CITED")
        status = "EXPLICIT_EARLIER_ANNOUNCEMENT_CONTEXT"
    elif case["temporal_scope"] == "OLDER_AUTHORIZATION":
        status = "OLDER_AUTHORIZATION_ORIGINAL_DISCLOSURE_UNRESOLVED"
    elif case["temporal_scope"] == "CURRENT_RELEASE":
        if not facts.get("stated_announcement_date"):
            raise ValueError("CURRENT_RELEASE_REQUIRES_STATED_ANNOUNCEMENT_DATE")
        if (
            facts["common_share_scope"] == "EXPLICIT_COMMON"
            and facts["open_market_permission"] == "EXPLICIT"
        ):
            status = "CURRENT_RELEASE_SOURCE_SCOPE_SUPPORTED"
        else:
            status = "CURRENT_RELEASE_SCOPE_UNRESOLVED"
    else:
        raise ValueError("TEMPORAL_SCOPE_NOT_REGISTERED")
    return {
        **case,
        "resolution_status": status,
        "development_context_resolution": True,
        "independent_QA_passed": False,
        "independent_reviewer": None,
        "research_qualifying": False,
        "deduplication_independently_certified": False,
        "first_public_dissemination_certified": False,
        "public_timestamp": None,
        "return_entry_session": None,
        "security_mapping_certified": False,
    }


def validate_membership(cases: list[dict[str, Any]], records: dict[str, Any]) -> None:
    assigned = [key for case in cases for key in case["members"]]
    if Counter(assigned) != Counter(records.keys()):
        raise ValueError("EVERY_SHORTLIST_MEMBER_MUST_BE_ASSIGNED_EXACTLY_ONCE")
    keys = {case["case_key"] for case in cases}
    if len(keys) != len(cases) or len(cases) > 30:
        raise ValueError("CASE_IDENTITY_OR_BOUND_EXCEEDED")
    by_key = {case["case_key"]: case for case in cases}
    for case in cases:
        relation = case.get("programme_relationship")
        if relation and (
            relation["other_case_key"] not in by_key
            or by_key[relation["other_case_key"]]["accession"] != case["accession"]
        ):
            raise ValueError("PROGRAMME_LINK_OUTSIDE_DOCUMENTED_FILING")
        seen = {case["case_key"]}
        parent = case.get("related_to")
        while parent:
            if (
                parent not in by_key
                or parent in seen
                or by_key[parent]["accession"] != case["accession"]
            ):
                raise ValueError("PROGRAMME_RELATIONSHIP_INVALID_OR_CYCLIC")
            seen.add(parent)
            parent = by_key[parent].get("related_to")


def preflight() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    _, baseline = ledger.preflight()
    registry = json.loads((PROTOCOL / "repurchase-context-extension-v1.json").read_bytes())
    if set(registry["files"]) != REGISTERED:
        raise ValueError("CONTEXT_REGISTRY_SCOPE_MISMATCH")
    for name, expected in registry["files"].items():
        if sec.file_sha(ROOT / name) != expected:
            raise ValueError("CONTEXT_REGISTERED_FILE_CHANGED")
    policy = json.loads((PROTOCOL / "repurchase-context-policy-v1.json").read_bytes())
    if (
        sec.file_sha(ledger.OUTPUT / "output-manifest.json") != LEDGER_SHA
        or sec.file_sha(INPUT) != policy["case_proposals_sha256"]
    ):
        raise ValueError("CONTEXT_INPUT_IDENTITY_CHANGED")
    manifest = json.loads((ledger.OUTPUT / "output-manifest.json").read_bytes())
    for name, expected in manifest["files"].items():
        path = (ledger.OUTPUT / name).resolve()
        if not path.is_relative_to(ledger.OUTPUT.resolve()) or sec.file_sha(path) != expected:
            raise ValueError("SOURCE_LEDGER_HASH_REPLAY_FAILED")
    data = json.loads(INPUT.read_bytes())
    records = {
        x["record_id"]: x
        for x in [
            json.loads(line)
            for line in (ledger.OUTPUT / "candidates.jsonl").read_bytes().splitlines()
        ]
    }
    if len(records) != 22 or data["source_ledger_manifest_sha256"] != LEDGER_SHA:
        raise ValueError("FROZEN_SHORTLIST_IDENTITY_MISMATCH")
    return data, records, baseline, registry


def materialize() -> dict[str, Any]:
    data, records, baseline, registry = preflight()
    sec.EXTENSION_FILES = (
        *sec.EXTENSION_FILES,
        *REGISTERED,
        *ledger.REGISTERED,
        *baseline["parser_files"],
    )
    sec.install_firewall()
    sys.addaudithook(
        lambda event, args: (
            (_ for _ in ()).throw(RuntimeError("CONTEXT_RESOLUTION_OFFLINE_ONLY"))
            if event.startswith(("socket.", "http.client.", "subprocess."))
            else None
        )
    )
    coverage = json.loads((ledger.OUTPUT / "source-coverage.json").read_bytes())
    components = {x["component_id"]: x for x in coverage["components"]}
    evidence = data["evidence"]
    source_cache = sec.Cache(ledger.acq.OUTPUT)
    source_raw: dict[str, bytes] = {}
    for key, item in evidence.items():
        component = components[item["component_id"]]
        text = (ledger.OUTPUT / component["derived_text_file"]).read_text(encoding="utf-8")
        ledger.verify_evidence(
            {
                "evidence": item,
                "accession": item["accession"],
                "container_sha256": item["container_sha256"],
                "certified_qualifying": False,
                "source_filing_date_is_public_time": False,
            },
            component,
            text,
        )
        sha = component["container_sha256"]
        if sha not in source_raw:
            source_raw[sha] = source_cache.read(sha)
        raw = source_raw[sha]
        if (
            sec.sha(raw[component["container_byte_start"] : component["container_byte_end"]])
            != item["source_document_sha256"]
            or key != item["evidence_id"]
        ):
            raise ValueError("CONTEXT_RAW_COMPONENT_REPLAY_FAILED")
    validate_membership(data["cases"], records)
    metadata = json.loads((ledger.acq.DISCOVERY / "bounded-source-manifest.json").read_bytes())
    entries = {x["accession"]: x for x in metadata["entries"]}
    shortlisted_accessions = {row["accession"] for row in records.values()}
    for case in data["cases"]:
        if (
            case["accession"] not in shortlisted_accessions
            or case["reporting_filer_cik"]
            != str(int(entries[case["accession"]]["cik_from_historical_index"]))
            or case["source_filed"] != entries[case["accession"]]["filed"]
        ):
            raise ValueError("CONTEXT_FILER_OR_FILING_DATE_IDENTITY_MISMATCH")
    cases = [validate_case(case, records, evidence) for case in data["cases"]]
    for case in cases:
        case["unresolved_requirements"] = [
            "INDEPENDENT_FACTUAL_QA",
            "FIRST_PUBLIC_DISSEMINATION_AND_INFORMATION_CLOCK",
            "HISTORICAL_SECURITY_MAPPING_AND_ELIGIBILITY",
        ]
        if case["resolution_status"] == "CURRENT_RELEASE_SCOPE_UNRESOLVED":
            case["unresolved_requirements"].append(
                "OPEN_MARKET_PERMISSION_NOT_ESTABLISHED_IN_CACHED_SOURCES"
            )
        if case["temporal_scope"] in {"OLDER_AUTHORIZATION", "EXPLICIT_EARLIER_ANNOUNCEMENT"}:
            case["unresolved_requirements"].append("ORIGINAL_ANNOUNCEMENT_SOURCE_NOT_ESTABLISHED")
        if case["facts"].get("approval_date") is None:
            case["unresolved_requirements"].append("EXACT_APPROVAL_DATE_UNRESOLVED_OR_PARTIAL")
    summary = {
        "version": "repurchase-context-resolution-v1",
        "status": "SOURCE_CONTEXT_RECONCILED_PENDING_INDEPENDENT_QA",
        "source_ledger_manifest_sha256": LEDGER_SHA,
        "case_proposals_sha256": sec.file_sha(INPUT),
        "source_shortlist_passages": 22,
        "source_filings": len({x["accession"] for x in cases}),
        "shortlist_action_groups": sum(bool(x["members"]) for x in cases),
        "additional_related_context_actions": sum(not x["members"] for x in cases),
        "action_records": len(cases),
        "resolution_status_counts": dict(Counter(x["resolution_status"] for x in cases)),
        "source_supported_evidence_spans": len(evidence),
        "first_public_times_certified": 0,
        "qualifying_research_events": 0,
        "independent_factual_decisions": 0,
        "independent_QA_passed": False,
        "deduplication_independently_certified": False,
        "original_718_sample_changes": 0,
        "new_network_requests": 0,
        "market_data_accessed": False,
        "full_extraction_authorized": False,
        "final_qa_authorized": False,
        "phase3_authorized": False,
        "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
    }

    def write(name: str, raw: bytes) -> None:
        ledger.immutable(OUTPUT / name, raw)

    write("actions.jsonl", b"".join(sec.json_bytes(x) for x in cases))
    write("evidence.json", sec.json_bytes(evidence))
    write("summary.json", sec.json_bytes(summary))
    write(
        "member-action-links.jsonl",
        b"".join(
            sec.json_bytes(
                {
                    "record_id": member,
                    "case_key": case["case_key"],
                    "link_basis": case["grouping_basis"],
                    "independently_certified": False,
                }
            )
            for case in cases
            for member in case["members"]
        ),
    )
    sections = []
    for case in cases:
        facts = case["facts"]
        capacities = "; ".join(x["as_stated"] + " [" + x["role"] + "]" for x in case["capacities"])
        proof_html = "".join(
            "<li><b>"
            + escape(evidence[k]["filename"])
            + "</b><blockquote>"
            + escape(evidence[k]["quote"])
            + "</blockquote></li>"
            for k in sorted({k for ids in case["support"].values() for k in ids})
        )
        sections.append(
            "<section><h2>"
            + escape(case["company"])
            + " — "
            + escape(case["case_key"])
            + "</h2><p>"
            + escape(case["resolution_status"])
            + "</p><p>"
            + escape(case["explanation"])
            + "</p><p>Stated announcement: "
            + escape(
                str(
                    facts.get("stated_announcement_date")
                    or facts.get("stated_announcement_month")
                    or "unresolved"
                )
            )
            + "; approval: "
            + escape(
                str(
                    facts.get("approval_date")
                    or facts.get("approval_date_as_stated")
                    or "unresolved"
                )
            )
            + "; filed: "
            + escape(case["source_filed"])
            + "</p><p>"
            + escape(capacities)
            + "</p><p>Open-market permission: "
            + escape(facts["open_market_permission"])
            + "; share class: "
            + escape(facts["common_share_scope"])
            + "</p><details><summary>Source evidence</summary><ul>"
            + proof_html
            + "</ul></details></section>"
        )
    report = (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'\">"
        "<title>Repurchase action reconciliation</title><style>"
        "body{font:16px/1.5 system-ui;max-width:1100px;margin:30px auto;padding:20px}"
        "section{border-top:1px solid #bbb;padding:12px}blockquote{white-space:pre-wrap}</style>"
        "<h1>Repurchase action reconciliation</h1><p>Development source-context findings. "
        "Dates are source-stated dates, not certified first-public timestamps. "
        "Independent factual QA and historical security eligibility remain open. "
        "No returns have been calculated.</p>" + "".join(sections) + "</html>"
    )
    write("actions.html", report.encode())
    files = {
        p.name: sec.file_sha(p)
        for p in OUTPUT.iterdir()
        if p.is_file() and p.name != "output-manifest.json"
    }
    write(
        "output-manifest.json",
        sec.json_bytes(
            {
                "files": files,
                "input_proposals_sha256": sec.file_sha(INPUT),
                "source_ledger_manifest_sha256": LEDGER_SHA,
                "code_registry": registry,
            }
        ),
    )
    return summary


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("command", choices=["build", "validate"])
    command = cli.parse_args().command
    try:
        result = materialize()
        print(json.dumps({"command": command, **result}), flush=True)
    except (ValueError, OSError, RuntimeError, KeyError, sec.AcquisitionError) as error:
        print(
            json.dumps(
                {
                    "status": "CONTEXT_RESOLUTION_STOPPED_NO_GATE_OPENED",
                    "reason": str(error)
                    if isinstance(error, (ValueError, sec.AcquisitionError))
                    else type(error).__name__,
                }
            ),
            flush=True,
        )
        raise SystemExit(2) from None
