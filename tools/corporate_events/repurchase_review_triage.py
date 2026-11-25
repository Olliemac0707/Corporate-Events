"""Explainable, uncalibrated review prioritisation for the frozen SEC pilot."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from html import escape
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "protocols/corporate-event-alpha-v1"
PRIVATE = ROOT / "reports/corporate-event-alpha-v1/phase2-private"
OUTPUT = PRIVATE / "repurchase-review-triage-v1"
CONTEXT_SHA = "165f5d64120c00f53964626b5a95ae225e4c7e4f42e1705f3cf4ed77d2d7ae4b"
REGISTERED = {
    "tools/corporate_events/repurchase_review_triage.py",
    "tests/test_repurchase_review_triage.py",
    "protocols/corporate-event-alpha-v1/repurchase-review-triage-policy-v1.json",
    "protocols/corporate-event-alpha-v1/repurchase-review-prioritisation-amendment-v1.json",
}
spec = importlib.util.spec_from_file_location(
    "triage_context", ROOT / "tools/corporate_events/repurchase_context_resolution.py"
)
assert spec and spec.loader
context: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = context
spec.loader.exec_module(context)
ledger, sec = context.ledger, context.sec


def rank(seed: str, stratum: str, accession: str) -> str:
    return hashlib.sha256(f"{seed}|{stratum}|{accession}".encode()).hexdigest()


def near_miss(row: dict[str, Any]) -> bool:
    if row["category"] == "CANDIDATE":
        return False
    text = ledger.rules.p.lexical(row["evidence"]["quote"])
    match = re.search(
        r"\b(?:board(?: of directors)?|company|we)\b.{0,180}?"
        r"\b(?:approved|authorized|authorised|approves|authorizes)\b"
        r".{0,240}?\brepurchas\w*",
        text,
        re.I,
    )
    if not match or not re.search(r"common\s+(?:stock|shares)", text, re.I):
        return False
    approval = re.search(r"approved|authorized|authorised|approves|authorizes", match[0], re.I)
    assert approval
    prefix = re.sub(
        r"\bMay\s+(?:19|20)[0-9]{2}\b",
        "<MONTH>",
        text[: match.start() + approval.start()],
        flags=re.I,
    )
    return not ledger.rules.p.modal_or_conditional(prefix) and not re.search(
        r"\bnot\b|\bnever\b", prefix, re.I
    )


def interpretation_flags(
    case: dict[str, Any], cases: list[dict[str, Any]], rows: dict[str, Any], weights: dict[str, int]
) -> list[dict[str, Any]]:
    flags = []

    def add(code: str, dimension: str, record_ids: list[str], evidence_ids: list[str]) -> None:
        flags.append(
            {
                "code": code,
                "dimension": dimension,
                "points": weights[code],
                "record_ids": record_ids,
                "evidence_ids": sorted(set(evidence_ids)),
            }
        )

    members = [rows[key] for key in case["members"]]
    expected = case["action_kind"]
    disagreement = [
        row["record_id"] for row in members if row["proposed_state"] not in {"UNRESOLVED", expected}
    ]
    if disagreement:
        add(
            "PARSER_CONTEXT_SUBTYPE_DISAGREEMENT",
            "SEMANTIC_INTERPRETATION",
            disagreement,
            case["support"].get("action_identity", []),
        )
    if case["temporal_scope"] == "NON_TARGET_ASR" and members:
        add(
            "CONTRACT_EXECUTION_VERSUS_AUTHORIZATION",
            "SEMANTIC_INTERPRETATION",
            case["members"],
            case["support"].get("action_identity", []),
        )
    signatures = {(Decimal(cap["value_decimal"]), cap["unit"]) for cap in case["capacities"]}
    peers = [
        other
        for other in cases
        if other["case_key"] != case["case_key"]
        and other["accession"] == case["accession"]
        and signatures
        & {(Decimal(cap["value_decimal"]), cap["unit"]) for cap in other["capacities"]}
    ]
    if peers:
        add(
            "EQUAL_CAPACITY_DISTINCT_ACTION_IDENTITY",
            "PROGRAMME_IDENTITY",
            case["members"],
            case["support"].get("capacity", []),
        )
    if len(members) > 1:
        add(
            "MULTIPLE_PASSAGES_GROUPED",
            "GROUPING",
            case["members"],
            case["support"].get("document_link", []),
        )
    multiple_dates = [
        row["record_id"]
        for row in members
        if len(ledger.rules.p.dates(row["evidence"]["quote"])) > 1
    ]
    if multiple_dates:
        add(
            "MULTIPLE_DATED_FACTS_IN_ACTION_PASSAGE",
            "DATE_ROLES",
            multiple_dates,
            case["support"].get("stated_announcement_date", []),
        )
    return flags


def points(flags: list[dict[str, Any]]) -> tuple[int, dict[str, int]]:
    # Correlated warning codes within one dimension do not stack.
    dimensions: dict[str, int] = {}
    for flag in flags:
        value = flag["points"]
        if type(value) is not int or not 0 <= value <= 100:
            raise ValueError("TRIAGE_WEIGHT_OUTSIDE_ORDINAL_SCALE")
        dimensions[flag["dimension"]] = max(dimensions.get(flag["dimension"], 0), value)
    return min(100, sum(dimensions.values())), dimensions


def score_cases(
    cases: list[dict[str, Any]], rows: list[dict[str, Any]], policy: dict[str, Any]
) -> list[dict[str, Any]]:
    by_id = {row["record_id"]: row for row in rows}
    result = []
    for case in cases:
        flags = interpretation_flags(case, cases, by_id, policy["weights"])
        score, dimensions = points(flags)
        followup = []
        if case["temporal_scope"] in {"OLDER_AUTHORIZATION", "EXPLICIT_EARLIER_ANNOUNCEMENT"}:
            followup.append("ORIGINAL_DISCLOSURE_SOURCE_REQUIRED")
        if (
            case["temporal_scope"] == "CURRENT_RELEASE"
            and case["facts"]["open_market_permission"] != "EXPLICIT"
        ):
            followup.append("OPEN_MARKET_PERMISSION_EVIDENCE_REQUIRED")
        if case["facts"].get("approval_date_precision") == "MONTH_DAY_YEAR_NOT_ASSIGNED":
            followup.append("MISSING_APPROVAL_YEAR_REMAINS_UNASSIGNED")
        result.append(
            {
                "case_key": case["case_key"],
                "accession": case["accession"],
                "risk_points": score,
                "risk_dimensions": dimensions,
                "flags": flags,
                "priority_review_flag": score >= policy["priority_threshold"],
                "source_evidence_followup": followup,
                "correctness_probability": None,
                "calibration_status": "UNCALIBRATED_NO_INDEPENDENT_LABELS",
                "research_qualifying": False,
                "automatic_acceptance": False,
            }
        )
    return result


def score_passages(
    rows: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    case_scores: list[dict[str, Any]],
    evidence: dict[str, Any],
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    member_scores = {
        member: score
        for case, score in zip(cases, case_scores, strict=True)
        for member in case["members"]
    }
    action_spans = [
        (case["accession"], evidence[key])
        for case in cases
        for key in case["support"].get("action_identity", [])
    ]
    results = []
    for row in rows:
        mapped = member_scores.get(row["record_id"])
        flags = list(mapped["flags"]) if mapped else []
        covered = any(
            acc == row["accession"]
            and ev["component_id"] == row["component_id"]
            and ev["text_start"] <= row["evidence"]["text_start"]
            and ev["text_end"] >= row["evidence"]["text_end"]
            for acc, ev in action_spans
        )
        if not covered and near_miss(row):
            flags.append(
                {
                    "code": "UNRESOLVED_OPERATIVE_COMMON_SHARE_APPROVAL",
                    "dimension": "SEMANTIC_INTERPRETATION",
                    "points": policy["weights"]["UNRESOLVED_OPERATIVE_COMMON_SHARE_APPROVAL"],
                    "record_ids": [row["record_id"]],
                    "evidence_ids": [row["evidence"]["evidence_id"]],
                }
            )
        score, dimensions = points(flags)
        results.append(
            {
                "record_id": row["record_id"],
                "accession": row["accession"],
                "risk_points": score,
                "risk_dimensions": dimensions,
                "flags": flags,
                "priority_review_flag": score >= policy["priority_threshold"],
                "correctness_probability": None,
                "automatic_acceptance": False,
            }
        )
    return results


def review_units(
    sources: list[dict[str, Any]],
    case_scores: list[dict[str, Any]],
    passage_scores: list[dict[str, Any]],
    threshold: int,
) -> list[dict[str, Any]]:
    units = {
        row["accession"]: {
            "accession": row["accession"],
            "role": row["role"],
            "risk_points": 0,
            "case_keys": [],
            "flagged_record_ids": [],
            "flag_codes": [],
            "source_evidence_followup": [],
        }
        for row in sources
    }
    for score in case_scores:
        unit = units[score["accession"]]
        unit["risk_points"] = max(unit["risk_points"], score["risk_points"])
        unit["case_keys"].append(score["case_key"])
        unit["flag_codes"].extend(flag["code"] for flag in score["flags"])
        unit["source_evidence_followup"].extend(score["source_evidence_followup"])
    for score in passage_scores:
        unit = units[score["accession"]]
        unit["risk_points"] = max(unit["risk_points"], score["risk_points"])
        if score["priority_review_flag"]:
            unit["flagged_record_ids"].append(score["record_id"])
        unit["flag_codes"].extend(flag["code"] for flag in score["flags"])
    for unit in units.values():
        for key in ("case_keys", "flagged_record_ids", "flag_codes", "source_evidence_followup"):
            unit[key] = sorted(set(unit[key]))
        unit["priority_review_flag"] = unit["risk_points"] >= threshold
        unit["research_qualifying"] = False
    return sorted(units.values(), key=lambda x: x["accession"])


def select_batch(units: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    seed = policy["audit_seed"]
    flagged = sorted(
        [u for u in units if u["priority_review_flag"]],
        key=lambda u: (-u["risk_points"], rank(seed, "priority", u["accession"])),
    )
    priority = flagged[: policy["first_wave_priority_filing_cap"]]
    low_pool = [u for u in units if not u["priority_review_flag"] and u["role"] == "CANDIDATE_HIT"]
    control_pool = [
        u for u in units if not u["priority_review_flag"] and u["role"] == "SOURCE_CONTROL"
    ]
    low = sorted(low_pool, key=lambda u: rank(seed, "lower-risk-audit", u["accession"]))[
        : policy["first_wave_lower_risk_audit_cap"]
    ]
    controls = sorted(control_pool, key=lambda u: rank(seed, "control-audit", u["accession"]))[
        : policy["first_wave_control_audit_cap"]
    ]
    selected = [
        {**u, "selection_stratum": stratum}
        for stratum, group in (
            ("PRIORITY_REVIEW", priority),
            ("LOWER_RISK_AUDIT", low),
            ("FIXED_CONTROL_AUDIT", controls),
        )
        for u in group
    ]
    if len({u["accession"] for u in selected}) != len(selected):
        raise ValueError("REVIEW_STRATA_OVERLAP")
    return {
        "selected": selected,
        "priority_backlog": flagged[len(priority) :],
        "lower_risk_audit_pool_size": len(low_pool),
        "control_audit_pool_size": len(control_pool),
        "all_priority_filings": len(flagged),
        "is_population_accuracy_estimate": False,
    }


def calibration_status() -> dict[str, Any]:
    return {
        "status": "NOT_EVALUATED_NO_INDEPENDENT_LABELS",
        "independent_labels": 0,
        "accuracy": None,
        "precision": None,
        "recall": None,
        "correctness_probability_calibrated": False,
        "selective_risk_measured": False,
        "QA_gate_passed": False,
    }


def source_packet(
    unit: dict[str, Any],
    rows: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    evidence: dict[str, Any],
    components: list[dict[str, Any]],
    seed: str,
    reader: Any,
) -> dict[str, Any]:
    acc = unit["accession"]
    component_map = {c["component_id"]: c for c in components if c["accession"] == acc}
    spans: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for case in cases:
        if case["accession"] != acc:
            continue
        for field in (
            "action_identity",
            "capacity",
            "document_link",
            "open_market_permission",
            "stated_announcement_date",
        ):
            for key in case["support"].get(field, []):
                ev = evidence[key]
                spans[ev["component_id"]].append((ev["text_start"], ev["text_end"]))
    for row in rows:
        if row["accession"] == acc and row["record_id"] in unit["flagged_record_ids"]:
            spans[row["component_id"]].append(
                (row["evidence"]["text_start"], row["evidence"]["text_end"])
            )
    if not spans:
        for row in [r for r in rows if r["accession"] == acc][:8]:
            spans[row["component_id"]].append(
                (row["evidence"]["text_start"], row["evidence"]["text_end"])
            )
    excerpts = []
    for component_id, intervals in sorted(spans.items()):
        component = component_map[component_id]
        text = str(reader(component["derived_text_file"]))
        merged: list[list[int]] = []
        for start, end in sorted(
            (max(0, start - 300), min(len(text), end + 600)) for start, end in intervals
        ):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        for start, end in merged:
            excerpts.append(
                {
                    "filename": component["filename"],
                    "component_id": component_id,
                    "text_start": start,
                    "text_end": end,
                    "text": text[start:end],
                }
            )
    if not excerpts:
        for component in component_map.values():
            if component["extraction_status"] == "VISIBLE_TEXT_EXTRACTED":
                text = str(reader(component["derived_text_file"]))
                excerpts.append(
                    {
                        "filename": component["filename"],
                        "component_id": component["component_id"],
                        "text_start": 0,
                        "text_end": min(len(text), 1800),
                        "text": text[:1800],
                    }
                )
                break
    source_sha = (
        next(iter(component_map.values())).get("container_sha256") if component_map else None
    )
    original_file = (
        ("../repurchase-acquisition-v1/objects/sha256/" + source_sha[:2] + "/" + source_sha)
        if source_sha
        else None
    )
    return {
        "packet_id": rank(seed, "blinded-packet", acc),
        "accession": acc,
        "original_container_file": original_file,
        "excerpts": excerpts,
        "components": [
            {
                "filename": c["filename"],
                "text_file": "../bounded-repurchase-ledger-v3/" + c["derived_text_file"],
                "extraction_status": c["extraction_status"],
            }
            for c in component_map.values()
        ],
        "source_completeness_certified": False,
    }


def validate_policy(policy: dict[str, Any]) -> None:
    if (
        type(policy["priority_threshold"]) is not int
        or not 0 < policy["priority_threshold"] <= 100
        or not re.fullmatch(r"[0-9a-f]{64}", policy["audit_seed"])
        or policy["automatic_acceptance"] is not False
    ):
        raise ValueError("TRIAGE_POLICY_THRESHOLD_SEED_OR_ACCEPTANCE_INVALID")
    caps = [
        policy[key]
        for key in (
            "first_wave_priority_filing_cap",
            "first_wave_lower_risk_audit_cap",
            "first_wave_control_audit_cap",
        )
    ]
    if any(type(value) is not int or value < 0 for value in caps) or sum(caps) > 10:
        raise ValueError("INITIAL_REVIEW_WORKLOAD_CAP_INVALID")


def preflight() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    _, _, baseline, _ = context.preflight()
    registry = json.loads((PROTOCOL / "repurchase-review-triage-extension-v1.json").read_bytes())
    if set(registry["files"]) != REGISTERED:
        raise ValueError("TRIAGE_REGISTRY_SCOPE_MISMATCH")
    for name, expected in registry["files"].items():
        if sec.file_sha(ROOT / name) != expected:
            raise ValueError("TRIAGE_REGISTERED_CODE_OR_POLICY_CHANGED")
    policy = json.loads((PROTOCOL / "repurchase-review-triage-policy-v1.json").read_bytes())
    validate_policy(policy)
    amendment = json.loads(
        (PROTOCOL / "repurchase-review-prioritisation-amendment-v1.json").read_bytes()
    )
    if (
        amendment["original_acceptance_gate_waived"] is not False
        or policy["automatic_acceptance"] is not False
    ):
        raise ValueError("TRIAGE_CANNOT_OPEN_RESEARCH_ACCEPTANCE_GATE")
    if sec.file_sha(context.OUTPUT / "output-manifest.json") != CONTEXT_SHA:
        raise ValueError("FROZEN_CONTEXT_RESOLUTION_CHANGED")
    manifest = json.loads((context.OUTPUT / "output-manifest.json").read_bytes())
    for name, expected in manifest["files"].items():
        path = (context.OUTPUT / name).resolve()
        if not path.is_relative_to(context.OUTPUT.resolve()) or sec.file_sha(path) != expected:
            raise ValueError("TRIAGE_CONTEXT_INPUT_REPLAY_FAILED")
    return policy, baseline, registry


def build() -> dict[str, Any]:
    policy, baseline, registry = preflight()
    sec.EXTENSION_FILES = (
        *sec.EXTENSION_FILES,
        *REGISTERED,
        *context.REGISTERED,
        *ledger.REGISTERED,
        *baseline["parser_files"],
    )
    sec.install_firewall()
    sys.addaudithook(
        lambda event, args: (
            (_ for _ in ()).throw(RuntimeError("TRIAGE_PIPELINE_OFFLINE_ONLY"))
            if event.startswith(("socket.", "http.client.", "subprocess."))
            else None
        )
    )
    cases = [json.loads(x) for x in (context.OUTPUT / "actions.jsonl").read_bytes().splitlines()]
    evidence = json.loads((context.OUTPUT / "evidence.json").read_bytes())
    rows = [json.loads(x) for x in (ledger.OUTPUT / "ledger.jsonl").read_bytes().splitlines()]
    coverage = json.loads((ledger.OUTPUT / "source-coverage.json").read_bytes())
    inventory = json.loads((ledger.acq.OUTPUT / "acquired-source-manifest.json").read_bytes())
    case_scores = score_cases(cases, rows, policy)
    passage_scores = score_passages(rows, cases, case_scores, evidence, policy)
    units = review_units(
        inventory["sources"], case_scores, passage_scores, policy["priority_threshold"]
    )
    batch = select_batch(units, policy)

    def write(name: str, value: Any, jsonl: bool = False) -> None:
        raw = b"".join(sec.json_bytes(row) for row in value) if jsonl else sec.json_bytes(value)
        ledger.immutable(OUTPUT / name, raw)

    write("case-risk-index.jsonl", case_scores, True)
    write("passage-risk-index.jsonl", passage_scores, True)
    write("filing-risk-index.jsonl", units, True)
    write("operator-review-queue.json", batch)
    write("calibration-status.json", calibration_status())
    # A separate packet contains source context without predictions, scores or strata.
    source_packets = []
    for unit in batch["selected"]:
        source_packets.append(
            source_packet(
                unit,
                rows,
                cases,
                evidence,
                coverage["components"],
                policy["audit_seed"],
                lambda name: (ledger.OUTPUT / name).read_text(encoding="utf-8"),
            )
        )
    source_packets.sort(key=lambda p: p["packet_id"])
    write(
        "source-only-review-packets.json",
        {
            "packets": source_packets,
            "reviewer": None,
            "decisions_recorded": 0,
            "predicted_labels_included": False,
            "coverage_warning": (
                "Excerpts aid navigation; full original context and retained "
                "non-text components may require inspection. "
                "Do not certify absence from excerpts alone."
            ),
        },
    )
    write(
        "blank-review-decisions.json",
        {
            "reviewer_id": None,
            "source_first_blind_review_confirmed": False,
            "packets": [
                {
                    "packet_id": p["packet_id"],
                    "accession": p["accession"],
                    "action_facts": None,
                    "dates_and_roles": None,
                    "capacity_and_class": None,
                    "missed_actions": None,
                    "source_completeness": None,
                    "uncertainty_notes": None,
                }
                for p in source_packets
            ],
        },
    )
    sections = []
    for packet in source_packets:
        excerpts_html = "".join(
            "<h3>"
            + escape(x["filename"])
            + "</h3><blockquote>"
            + escape(x["text"])
            + "</blockquote>"
            for x in packet["excerpts"]
        )
        links = "".join(
            '<li><a href="'
            + escape(c["text_file"], quote=True)
            + '">'
            + escape(c["filename"])
            + "</a> — "
            + escape(c["extraction_status"])
            + "</li>"
            for c in packet["components"]
        )
        sections.append(
            "<section><h2>Source "
            + escape(packet["packet_id"][:12])
            + "</h2><p>Accession "
            + escape(packet["accession"])
            + "</p><p>Identify the actions described, date roles, capacity, share class "
            "and programme relationships. Mark unsupported facts as unresolved.</p>"
            + (
                '<p><a href="'
                + escape(packet["original_container_file"], quote=True)
                + '">Original complete submission</a></p>'
                if packet["original_container_file"]
                else ""
            )
            + excerpts_html
            + "<details><summary>All extracted components</summary><ul>"
            + links
            + "</ul></details></section>"
        )
    page = (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'\">"
        "<title>Source-only factual review</title><style>"
        "body{font:16px/1.5 system-ui;max-width:1100px;margin:auto;padding:25px}"
        "blockquote{white-space:pre-wrap}section{border-top:1px solid #bbb;padding:15px}</style>"
        "<h1>Source-only factual review</h1><p>This packet contains source context, "
        "with proposed classifications and risk scores withheld. Excerpts are navigation aids; "
        "Inspect complete context and record uncertainty. "
        "A blank result is not a negative finding. "
        "No independent decisions have been recorded.</p>" + "".join(sections) + "</html>"
    )
    ledger.immutable(OUTPUT / "source-only-review.html", page.encode())
    table = "".join(
        "<tr><td>"
        + escape(u["accession"])
        + "</td><td>"
        + str(u["risk_points"])
        + "</td><td>"
        + escape(u["selection_stratum"])
        + "</td><td>"
        + escape(", ".join(u["flag_codes"]))
        + "</td></tr>"
        for u in batch["selected"]
    )
    operator = (
        '<!doctype html><meta charset="utf-8"><title>Review triage operator queue</title>'
        "<h1>Review triage operator queue</h1><p>Uncalibrated risk points. "
        "Scores are not correctness probabilities and do not approve research events. "
        "Give independent reviewers only source-only-review.html.</p>"
        "<table><tr><th>Source</th><th>Risk points</th><th>Stratum</th><th>Flags</th></tr>"
        + table
        + "</table>"
    )
    ledger.immutable(OUTPUT / "operator-queue.html", operator.encode())
    summary = {
        "version": "repurchase-review-triage-v1",
        "status": "UNCALIBRATED_REVIEW_PRIORITY_BATCH_FROZEN",
        "context_input_manifest_sha256": CONTEXT_SHA,
        "policy_sha256": sec.file_sha(PROTOCOL / "repurchase-review-triage-policy-v1.json"),
        "case_records_scored": len(case_scores),
        "source_passages_scored": len(passage_scores),
        "filings_scored": len(units),
        "priority_threshold": policy["priority_threshold"],
        "flagged_cases": sum(x["priority_review_flag"] for x in case_scores),
        "flagged_passages": sum(x["priority_review_flag"] for x in passage_scores),
        "flagged_filings": batch["all_priority_filings"],
        "first_wave_packets": len(batch["selected"]),
        "first_wave_strata": dict(Counter(u["selection_stratum"] for u in batch["selected"])),
        "priority_backlog_filings": len(batch["priority_backlog"]),
        "source_followup_cases": sum(bool(x["source_evidence_followup"]) for x in case_scores),
        "reviewer_assigned": False,
        "independent_labels": 0,
        "calibration_status": calibration_status()["status"],
        "correctness_probabilities_available": False,
        "automatic_acceptance": False,
        "review_sampling_is_not_population_accuracy_estimate": True,
        "original_718_sample_changes": 0,
        "research_qualifying_events": 0,
        "original_acceptance_gate_waived": False,
        "pipeline_network_requests": 0,
        "market_data_accessed": False,
        "full_extraction_authorized": False,
        "final_qa_authorized": False,
        "phase3_authorized": False,
        "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
    }
    write("summary.json", summary)
    files = {
        p.name: sec.file_sha(p)
        for p in OUTPUT.iterdir()
        if p.is_file() and p.name != "output-manifest.json"
    }
    write(
        "output-manifest.json",
        {"files": files, "code_registry": registry, "context_input_manifest_sha256": CONTEXT_SHA},
    )
    return summary


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("command", choices=["build", "validate"])
    command = cli.parse_args().command
    try:
        print(json.dumps({"command": command, **build()}), flush=True)
    except (ValueError, KeyError, OSError, RuntimeError, sec.AcquisitionError) as error:
        reason = (
            str(error)
            if isinstance(error, (ValueError, sec.AcquisitionError))
            else type(error).__name__
        )
        print(
            json.dumps({"status": "TRIAGE_STOPPED_NO_RESEARCH_GATE_OPENED", "reason": reason}),
            flush=True,
        )
        raise SystemExit(2) from None
