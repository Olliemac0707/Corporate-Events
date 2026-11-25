"""Confidence routing for explicitly identified AUTOMATED development assessments."""

from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "protocols/corporate-event-alpha-v1"
PILOT = ROOT / "reports/corporate-event-alpha-v1/phase2-private/parser-qa-v1"
RUN = PILOT / "runs/development-parse-v2"
OUTPUT = PILOT / "automated-confidence-review-v2"
POLICY_FILE = "protocols/corporate-event-alpha-v1/development-confidence-review-policy-v2.json"
REGISTERED = (
    "tools/corporate_events/confidence_review.py",
    "tests/test_confidence_review.py",
    POLICY_FILE,
)

spec = importlib.util.spec_from_file_location(
    "cea_review_administration", ROOT / "tools/corporate_events/review_admin.py"
)
assert spec and spec.loader
admin: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = admin
spec.loader.exec_module(admin)
workflow = admin.workflow


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def require(ok: object, reason: str) -> None:
    if not ok:
        raise ValueError(reason)


def read(path: Path) -> Any:
    return json.loads(path.read_bytes())


def exclusive(path: Path, value: object) -> None:
    raw = canonical(value)
    if path.exists():
        require(path.read_bytes() == raw, "IMMUTABLE_CONFIDENCE_INPUT_CONFLICT")
    else:
        workflow.sec.write_new(path, raw)


def preflight() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    qa = dict(admin.verify_frozen())
    proof = dict(workflow.preflight())
    extension = read(PROTOCOL / "development-confidence-review-extension-v2.json")
    require(set(extension["files"]) == set(REGISTERED), "CONFIDENCE_EXTENSION_SCOPE_INVALID")
    for name, expected in extension["files"].items():
        require(workflow.sec.file_sha(ROOT / name) == expected, "CONFIDENCE_CODE_HASH_MISMATCH")
    old = read(PROTOCOL / "review-admin-extension.json")
    require(
        set(old["files"])
        == {"tools/corporate_events/review_admin.py", "tests/test_review_admin.py"},
        "ADMIN_EXTENSION_SCOPE_INVALID",
    )
    for name, expected in old["files"].items():
        require(workflow.sec.file_sha(ROOT / name) == expected, "ADMIN_CODE_HASH_MISMATCH")
    policy: dict[str, Any] = read(ROOT / POLICY_FILE)
    require(
        policy["sample_sha256"] == admin.EXPECTED_SHA
        and policy["sample_records"] == 718
        and policy["sample_cells"] == 297
        and policy["parse_run"] == "development-parse-v2"
        and policy["authorization"]["automated_review_authorized"] is True,
        "CONFIDENCE_POLICY_IDENTITY_INVALID",
    )
    require(
        type(policy["routing"]["manual_below"]) is int
        and 0 <= policy["routing"]["manual_below"] <= 100,
        "CONFIDENCE_THRESHOLD_INVALID",
    )
    require(
        not policy["full_extraction_authorized"]
        and not policy["final_qa_authorized"]
        and not policy["phase3_authorized"]
        and not policy["market_data_allowed"]
        and not policy["new_network_requests_allowed"],
        "CONFIDENCE_SCOPE_INVALID",
    )
    proof["confidence_code_hashes"] = extension["files"]
    proof["policy_sha256"] = workflow.sec.file_sha(ROOT / POLICY_FILE)
    return qa, policy, proof


def install_guard() -> None:
    workflow.sec.EXTENSION_FILES = (
        *workflow.sec.EXTENSION_FILES,
        *workflow.REGISTERED,
        "tools/corporate_events/phase2.py",
        "tools/corporate_events/review_admin.py",
        *REGISTERED,
    )
    workflow.sec.install_firewall()


def prepare(qa: dict[str, Any], policy: dict[str, Any], proof: dict[str, Any]) -> dict[str, Any]:
    rows = admin.batches(qa)
    selected = {x["source_id"]: x for x in qa["selected"]}
    for row in rows:
        row["source_parse_status"] = selected[row["source_id"]]["parse_status"]
    frame = {
        "sample_sha256": admin.EXPECTED_SHA,
        "policy_sha256": proof["policy_sha256"],
        "policy": policy,
        "records": rows,
    }
    exclusive(OUTPUT / "frame.json", frame)
    (OUTPUT / "inbox").mkdir(exist_ok=True)
    if not (OUTPUT / "automated-assessments.jsonl").exists():
        workflow.sec.write_new(OUTPUT / "automated-assessments.jsonl", b"")
    return frame


def validate_and_route(
    item: dict[str, Any],
    case: dict[str, Any],
    source: bytes,
    policy: dict[str, Any],
    vocabulary: dict[str, list[str]],
) -> dict[str, Any]:
    expected = {
        "sample_record_id",
        "reviewer_id",
        "reviewer_kind",
        "model_id",
        "finding",
        "classification_state",
        "confidence",
        "coverage",
        "source_role_status",
        "essential_checks",
        "evidence_spans",
        "flags",
        "rationale",
        "supersedes_assessment_id",
    }
    require(set(item) == expected, "ASSESSMENT_FIELDS_INVALID")
    require(item["sample_record_id"] == case["sample_record_id"], "ASSESSMENT_SAMPLE_ID_INVALID")
    require(
        item["reviewer_kind"] == "AUTOMATED"
        and item["reviewer_id"] == policy["automated_reviewer_id"],
        "EXPLICIT_AUTOMATED_IDENTITY_REQUIRED",
    )
    require(item["model_id"] is None or isinstance(item["model_id"], str), "MODEL_ID_INVALID")
    require(digest(source) == case["source_document_sha256"], "ORIGINAL_SOURCE_HASH_MISMATCH")
    for name in ("finding", "rationale"):
        require(
            isinstance(item[name], str) and bool(item[name].strip()), "AUTOMATED_RATIONALE_REQUIRED"
        )
    state = item["classification_state"]
    require(isinstance(state, str) and state in vocabulary[case["family"]], "FAMILY_STATE_INVALID")
    confidence = item["confidence"]
    require(
        isinstance(confidence, dict) and set(confidence) == {"classification", "evidence"},
        "CONFIDENCE_DIMENSIONS_INVALID",
    )
    require(
        all(type(x) is int and 0 <= x <= 100 for x in confidence.values()),
        "CONFIDENCE_MUST_BE_INTEGER_0_TO_100",
    )
    require(
        item["coverage"] in {"FULL_SOURCE_REVIEWED", "PARTIAL_SOURCE_REVIEWED", "UNSUPPORTED"},
        "SOURCE_COVERAGE_INVALID",
    )
    require(item["source_role_status"] in {"RESOLVED", "UNRESOLVED"}, "SOURCE_ROLE_STATUS_INVALID")
    checks = item["essential_checks"]
    require(
        isinstance(checks, dict) and set(checks) == set(policy["essential_checks"][case["family"]]),
        "FAMILY_ESSENTIAL_CHECKS_REQUIRED",
    )
    for check in checks.values():
        require(
            isinstance(check, dict) and set(check) == {"status", "rationale"},
            "ESSENTIAL_CHECK_FIELDS_INVALID",
        )
        require(
            check["status"] in policy["essential_check_statuses"]
            and isinstance(check["rationale"], str)
            and bool(check["rationale"].strip()),
            "ESSENTIAL_CHECK_STATUS_AND_RATIONALE_REQUIRED",
        )
    flags = item["flags"]
    require(
        isinstance(flags, list)
        and all(isinstance(x, str) for x in flags)
        and set(flags) <= set(policy["always_manual_flags"])
        and len(set(flags)) == len(flags),
        "CRITICAL_FLAGS_INVALID",
    )
    reasons = set(flags)
    if state in policy["unresolved_classification_states"]:
        reasons.add("CLASSIFICATION_UNRESOLVED")
    if item["coverage"] != "FULL_SOURCE_REVIEWED":
        reasons.add("PARTIAL_SOURCE_COVERAGE")
    if (
        item["coverage"] == "UNSUPPORTED"
        or case["source_parse_status"] == "UNSUPPORTED_COMPONENT_RETAINED"
    ):
        reasons.add("UNSUPPORTED_SOURCE_COMPONENT")
    if item["source_role_status"] != "RESOLVED":
        reasons.add("SOURCE_ROLE_UNRESOLVED")
    statuses = [value["status"] for value in checks.values()]
    if "UNRESOLVED" in statuses or "RESOLVED" not in statuses:
        reasons.add("ESSENTIAL_FACT_UNRESOLVED")
    if "CONFLICTING" in statuses:
        reasons.add("CONFLICTING_EVIDENCE")
    spans = item["evidence_spans"]
    require(isinstance(spans, list), "EVIDENCE_SPANS_MUST_BE_LIST")
    for span in spans:
        require(
            isinstance(span, dict) and set(span) == {"byte_start", "byte_end", "sha256"},
            "EVIDENCE_SPAN_FIELDS_INVALID",
        )
        start, end = span["byte_start"], span["byte_end"]
        require(
            type(start) is int and type(end) is int and 0 <= start < end <= len(source),
            "EVIDENCE_BYTE_RANGE_INVALID",
        )
        require(digest(source[start:end]) == span["sha256"], "EVIDENCE_SPAN_HASH_INVALID")
    if not spans:
        reasons.add("EVIDENCE_MISSING")
    overall = min(confidence.values())
    if overall < policy["routing"]["manual_below"]:
        reasons.add("BELOW_CONFIDENCE_THRESHOLD")
    return {
        "overall_score": overall,
        "threshold": policy["routing"]["manual_below"],
        "route": "MANUAL_REVIEW_REQUIRED" if reasons else "AUTOMATED_REVIEWED_DEVELOPMENT",
        "manual_reasons": sorted(reasons),
        "classification_confidence": confidence["classification"],
        "evidence_confidence": confidence["evidence"],
        "timing_status": "TIMESTAMP_UNRESOLVED",
        "timing_confidence": None,
        "calibration": "UNCALIBRATED_OPERATIONAL_SCORE",
    }


def history(frame: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    path = OUTPUT / "automated-assessments.jsonl"
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    cases = {row["sample_record_id"]: row for row in frame["records"]}
    latest: dict[str, dict[str, Any]] = {}
    previous = None
    for row in rows:
        payload = dict(row)
        identifier = payload.pop("assessment_id")
        require(
            identifier == digest(canonical(payload)) and row["previous_assessment_id"] == previous,
            "AUTOMATED_ASSESSMENT_CHAIN_INVALID",
        )
        require(
            row["sample_record_id"] in cases and row["policy_sha256"] == frame["policy_sha256"],
            "AUTOMATED_ASSESSMENT_FRAME_INVALID",
        )
        item = row["assessment"]
        case = cases[row["sample_record_id"]]
        require(
            item["sample_record_id"] == case["sample_record_id"]
            and item["reviewer_kind"] == "AUTOMATED"
            and item["reviewer_id"] == frame["policy"]["automated_reviewer_id"]
            and row["source_document_sha256"] == case["source_document_sha256"],
            "AUTOMATED_ASSESSMENT_IDENTITY_INVALID",
        )
        require(
            digest((OUTPUT / "inputs" / (row["input_sha256"] + ".json")).read_bytes())
            == row["input_sha256"],
            "AUTOMATED_ASSESSMENT_INPUT_CHANGED",
        )
        old = latest.get(row["sample_record_id"])
        require(
            item["supersedes_assessment_id"] == (old["assessment_id"] if old else None),
            "AUTOMATED_SUPERSESSION_MUST_REFERENCE_LATEST",
        )
        latest[row["sample_record_id"]] = row
        previous = identifier
    return rows, latest


def record(name: str, frame: dict[str, Any], proof: dict[str, Any]) -> dict[str, Any]:
    require(
        bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\.json", name)),
        "LOCAL_ASSESSMENT_FILENAME_REQUIRED",
    )
    item = read(OUTPUT / "inbox" / name)
    require(isinstance(item, dict), "ASSESSMENT_OBJECT_REQUIRED")
    case = next(
        (x for x in frame["records"] if x["sample_record_id"] == item.get("sample_record_id")), None
    )
    require(case is not None, "ASSESSMENT_NOT_IN_FROZEN_SAMPLE")
    assert case is not None
    rows, latest = history(frame)
    if case["role"] == "PARSER_CANDIDATE":
        require(
            all(
                row["sample_record_id"] in latest
                for row in frame["records"]
                if row["role"] == "INDEPENDENT_SOURCE_CONTROL"
            ),
            "AUTOMATED_CONTROL_FIRST_BARRIER_CLOSED",
        )
    if case["sample_record_id"] not in latest:
        first_pending = next(
            row for row in frame["records"] if row["sample_record_id"] not in latest
        )
        require(
            case["sample_record_id"] == first_pending["sample_record_id"],
            "AUTOMATED_FROZEN_REVIEW_ORDER_REQUIRED",
        )
    source = (RUN / "documents" / (case["source_document_sha256"] + ".txt")).read_bytes()
    manifest = read(RUN / "run-manifest.json")
    require(
        manifest["original_component_hashes"][case["source_document_sha256"] + ".txt"]
        == case["source_document_sha256"],
        "SOURCE_NOT_IN_ORIGINAL_RUN",
    )
    result = validate_and_route(item, case, source, frame["policy"], admin.labels())
    input_sha = digest(canonical(item))
    old = latest.get(case["sample_record_id"])
    if old and old["input_sha256"] == input_sha:
        return {"status": "IDENTICAL_AUTOMATED_ASSESSMENT_ALREADY_RECORDED", **old["routing"]}
    require(
        item["supersedes_assessment_id"] == (old["assessment_id"] if old else None),
        "AUTOMATED_SUPERSESSION_MUST_REFERENCE_LATEST",
    )
    exclusive(OUTPUT / "inputs" / (input_sha + ".json"), item)
    value = {
        "sample_record_id": case["sample_record_id"],
        "family": case["family"],
        "role": case["role"],
        "source_id": case["source_id"],
        "source_document_sha256": case["source_document_sha256"],
        "policy_sha256": frame["policy_sha256"],
        "code_commit": proof["code_commit"],
        "code_hashes": proof["confidence_code_hashes"],
        "input_sha256": input_sha,
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "assessment": item,
        "routing": result,
        "previous_assessment_id": rows[-1]["assessment_id"] if rows else None,
    }
    value["assessment_id"] = digest(canonical(value))
    workflow.sec.append_record(OUTPUT / "automated-assessments.jsonl", value)
    return {
        "status": "AUTOMATED_ASSESSMENT_APPENDED",
        "assessment_id": value["assessment_id"],
        **result,
    }


def status(frame: dict[str, Any]) -> dict[str, Any]:
    _, latest = history(frame)
    manual = [
        latest[row["sample_record_id"]]
        for row in frame["records"]
        if row["sample_record_id"] in latest
        and latest[row["sample_record_id"]]["routing"]["route"] == "MANUAL_REVIEW_REQUIRED"
    ]
    high = [
        row
        for row in latest.values()
        if row["routing"]["route"] == "AUTOMATED_REVIEWED_DEVELOPMENT"
    ]
    pending = [
        row["sample_record_id"] for row in frame["records"] if row["sample_record_id"] not in latest
    ]
    suggestions = []
    if not pending:
        suggestions = sorted(
            high,
            key=lambda row: digest(
                (frame["policy_sha256"] + "|" + row["sample_record_id"]).encode()
            ),
        )[: frame["policy"]["routing"]["optional_high_score_spot_checks"]]
    human_path = RUN / "fact-review-history.jsonl"
    human = (
        [json.loads(line) for line in human_path.read_bytes().splitlines()]
        if human_path.exists()
        else []
    )
    counts: dict[str, Counter[str]] = {}
    for case in frame["records"]:
        family_count = counts.setdefault(case["family"], Counter())
        family_count["expected"] += 1
        row = latest.get(case["sample_record_id"])
        family_count[row["routing"]["route"] if row else "AUTOMATED_REVIEW_PENDING"] += 1
    result = {
        "status": "AUTOMATED_REVIEW_IN_PROGRESS"
        if pending
        else "AUTOMATED_PASS_COMPLETE_MANUAL_VERIFICATION_PENDING",
        "sample_sha256": frame["sample_sha256"],
        "policy_sha256": frame["policy_sha256"],
        "sample_records": len(frame["records"]),
        "threshold": frame["policy"]["routing"]["manual_below"],
        "score_calibration": "UNCALIBRATED_OPERATIONAL_SCORE",
        "automated_assessed": len(latest),
        "automated_review_pending": len(pending),
        "above_threshold_clear": len(high),
        "manual_review_records": len(manual),
        "manual_review_unique_sources": len({row["source_id"] for row in manual}),
        "manual_reasons": dict(
            Counter(reason for row in manual for reason in row["routing"]["manual_reasons"])
        ),
        "human_review_records": len({(x["source_id"], x["family"], x["role"]) for x in human}),
        "human_accuracy": "NOT_MEASURED",
        "human_precision": "NOT_MEASURED",
        "recall": "NOT_MEASURED",
        "probability_calibration": "NOT_MEASURED",
        "counts_by_family": {key: dict(value) for key, value in counts.items()},
        "source_preparation_warnings": sum(
            row["source_parse_status"] == "UNSUPPORTED_COMPONENT_RETAINED"
            for row in frame["records"]
        ),
        "pending_record_ids": pending,
        "optional_high_score_spot_check_record_ids": [x["sample_record_id"] for x in suggestions],
        "timing_status": "TIMESTAMP_UNRESOLVED",
        "timing_confidence": None,
        "full_extraction_authorized": False,
        "final_qa_authorized": False,
        "phase3_authorized": False,
        "parser_changes": 0,
        "classifier_changes": 0,
        "qa_redraws": 0,
        "qa_replacements": 0,
        "new_live_filings": 0,
        "network_requests": 0,
        "firewall_counters": dict.fromkeys(workflow.sec.FIREWALL_COUNTERS, 0),
    }
    (OUTPUT / "status.json").write_bytes(canonical(result))
    (OUTPUT / "manual-review-queue.json").write_bytes(canonical(manual))
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in manual:
        groups.setdefault(item["source_id"], []).append(item)
    cards = []
    for source_id, members in groups.items():
        details = []
        for row in members:
            assessment = row["assessment"]
            details.append(
                "<li>"
                + html.escape(row["family"])
                + " — score "
                + str(row["routing"]["overall_score"])
                + "/100"
                + "<p>"
                + html.escape(assessment["finding"])
                + "</p><p>"
                + html.escape(", ".join(row["routing"]["manual_reasons"]))
                + "</p></li>"
            )
        cards.append(
            "<section><h2>Source review</h2><a href='../runs/development-parse-v2/packets/"
            + source_id
            + ".control.html'>Open source</a><ul>"
            + "".join(details)
            + "</ul></section>"
        )
    page = (
        "<!doctype html><meta charset='utf-8'><meta http-equiv='Content-Security-Policy' "
        "content=\"default-src 'none'; style-src 'unsafe-inline'\">"
        "<title>Manual review queue</title>"
        "<style>body{max-width:850px;margin:2rem auto;padding:0 1rem;font:17px system-ui}"
        "section{border-top:1px solid #aaa;padding:1rem 0}</style><h1>Manual review queue</h1><p>"
        + str(len(manual))
        + " flagged records across "
        + str(len(groups))
        + " sources. "
        + str(len(pending))
        + " records await AUTOMATED assessment.</p><p>Read the source and send your "
        "finding and reason in the chat. The displayed finding was generated by AUTOMATED.</p>"
        + "".join(cards)
    )
    (OUTPUT / "manual-review.html").write_text(page, encoding="utf-8", newline="\n")
    lines = [
        "# Confidence-based development review",
        "",
        f"AUTOMATED-assessed {len(latest)} / {len(frame['records'])}. Pending {len(pending)}.",
        f"Manual verification {len(manual)} records across {len(groups)} sources.",
        f"Above threshold with resolved checks {len(high)}.",
        "",
        f"Provisional threshold {result['threshold']}/100. Overall score is the minimum "
        "of classification confidence and evidence confidence. Critical issues always "
        "route to manual review.",
        "",
        "Scores are reviewer-assigned operational measures. Human-labelled calibration "
        "and accuracy measurement are pending. The optional high-score audit list is "
        "selected after the AUTOMATED pass.",
        "",
        "Public dissemination timing is tracked separately as unresolved. "
        "Full extraction, final QA and Phase 3 require their registered later gates.",
        "",
        "Source-only AUTOMATED assessments precede candidate assessment recording. "
        "AUTOMATED findings are in automated-assessments.jsonl. Human decisions use the registered "
        "human recording mechanism.",
        "",
        "| Family | Expected | AUTOMATED reviewed | Manual queue | Pending |",
        "|---|---:|---:|---:|---:|",
    ]
    for family, count in counts.items():
        lines.append(
            f"| {family} | {count['expected']} | "
            f"{count['AUTOMATED_REVIEWED_DEVELOPMENT']} | {count['MANUAL_REVIEW_REQUIRED']} | "
            f"{count['AUTOMATED_REVIEW_PENDING']} |"
        )
    lines += ["", "All outcome-firewall counters are zero.", ""]
    (OUTPUT / "report.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return result


def main() -> int:
    if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode:
        print("Use python -I -S -B tools/corporate_events/confidence_review.py", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "record", "status"))
    parser.add_argument("--input", default="")
    args = parser.parse_args()
    owned = False
    try:
        qa, policy, proof = preflight()
        install_guard()
        OUTPUT.mkdir(parents=True, exist_ok=True)
        workflow.sec.write_new(OUTPUT / "review.lock", canonical({"pid": os.getpid()}))
        owned = True
        frame = prepare(qa, policy, proof)
        result = (
            record(args.input, frame, proof)
            if args.command == "record"
            else {"status": "CONFIDENCE_WORKFLOW_READY"}
        )
        progress = status(frame)
        print(
            json.dumps(
                {
                    **result,
                    "automated_assessed": progress["automated_assessed"],
                    "automated_review_pending": progress["automated_review_pending"],
                    "manual_review_records": progress["manual_review_records"],
                    "above_threshold_clear": progress["above_threshold_clear"],
                    "full_extraction_authorized": False,
                }
            )
        )
        return 0
    except (ValueError, KeyError, TypeError, OSError, workflow.sec.AcquisitionError) as error:
        print(
            json.dumps(
                {
                    "status": "CONFIDENCE_REVIEW_STOPPED",
                    "reason": str(error) if isinstance(error, ValueError) else type(error).__name__,
                    "full_extraction_authorized": False,
                }
            ),
            file=sys.stderr,
        )
        return 2
    finally:
        if owned:
            (OUTPUT / "review.lock").unlink()


if __name__ == "__main__":
    raise SystemExit(main())
