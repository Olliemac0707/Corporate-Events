"""Administrative first-pass human review only; never classify SEC documents."""

from __future__ import annotations

import argparse
import builtins
import hashlib
import html
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "protocols/corporate-event-alpha-v1"
PILOT = ROOT / "reports/corporate-event-alpha-v1/phase2-private/parser-qa-v1"
RUN = PILOT / "runs/development-parse-v2"
ADMIN = PILOT / "human-review-v1"
EXPECTED_SHA = "5da0f6c6061ebea6ac08e57e598e670636d634dd7b1dc158767fba135be073ae"
EXPECTED_RECORDS = 718
EXPECTED_CELLS = 297
RUN_ID = "development-parse-v2"
REVIEW_KEYS = {
    "source_id",
    "event_id",
    "reviewer_id",
    "decision",
    "reason",
    "evidence_ids",
    "supersedes_review_id",
    "scope",
    "family",
    "role",
    "correct_state",
    "event_presence",
}
TERMINAL = {"REVIEWED", "REVIEWED_REQUIRES_ADJUDICATION", "REVIEWED_UNRESOLVED"}
TAXONOMY = (
    "CONTAINER_RESOLUTION",
    "DOCUMENT_SELECTION",
    "STRUCTURAL_PARSE",
    "FACT_EXTRACTION",
    "EVIDENCE_LOCATOR",
    "FORM_ALIAS",
    "FAMILY_CLASSIFICATION",
    "CONTROLLED_STATE",
    "BASELINE_CHRONOLOGY",
    "REPORTER_SUBJECT_ROLE",
    "FORM4_TABLE",
    "FORM4_PLAN_VENUE",
    "REPURCHASE_RECAP_VS_AUTHORIZATION",
    "DIVIDEND_FREQUENCY",
    "SPINOFF_FIRST_DISCLOSURE",
    "DEDUP_ACTION_ANCHOR",
    "TIMESTAMP_AUTHORITY",
    "OTHER_REGISTERED_FACT_ERROR",
)
CELL_KEYS = ("family", "era", "form", "source_kind", "role")
ORDER_RULE = (
    "Stable partition of existing frozen selected-array order: all "
    "INDEPENDENT_SOURCE_CONTROL records first, then PARSER_CANDIDATE; "
    "consecutive groups of 25, final remainder. No reranking or replacements."
)


def load_workflow() -> Any:
    spec = importlib.util.spec_from_file_location(
        "cea_registered_review", ROOT / "tools/corporate_events/parser_qa.py"
    )
    if spec is None or spec.loader is None:
        raise ValueError("REGISTERED_REVIEW_WORKFLOW_MISSING")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


workflow = load_workflow()


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def require(ok: object, code: str) -> None:
    if not ok:
        raise ValueError(code)


def read(path: Path) -> Any:
    return json.loads(path.read_bytes())


def journal(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_bytes().splitlines()] if path.exists() else []


def write_new(path: Path, value: object) -> None:
    workflow.sec.write_new(path, canonical(value))


def sample_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return row["source_id"], row["family"], row["role"]


def verify_frozen() -> dict[str, Any]:
    raw = (PILOT / "frozen-development-qa-selection.json").read_bytes()
    require(digest(raw) == EXPECTED_SHA, "B_FROZEN_DEVELOPMENT_QA_IDENTITY_FAILURE")
    qa: dict[str, Any] = json.loads(raw)
    require(
        raw == (RUN / "development-qa-selection.json").read_bytes(),
        "B_FROZEN_DEVELOPMENT_QA_IDENTITY_FAILURE",
    )
    report = read(RUN / "parser-development-report.json")
    cells = Counter(tuple(row[k] for k in CELL_KEYS) for row in qa["selected"])
    require(
        report["run_id"] == RUN_ID
        and report["frozen_development_qa_sha256"] == EXPECTED_SHA
        and len(qa["selected"]) == EXPECTED_RECORDS
        and len(cells) == EXPECTED_CELLS
        and len(qa["cells"]) == EXPECTED_CELLS
        and max(cells.values()) <= 5
        and len({sample_key(row) for row in qa["selected"]}) == EXPECTED_RECORDS
        and report["qa_selected_records"] == EXPECTED_RECORDS
        and report["qa_cells"] == EXPECTED_CELLS
        and qa["final_qa_sample"] is False,
        "B_FROZEN_DEVELOPMENT_QA_IDENTITY_FAILURE",
    )
    base_extension = read(PROTOCOL / "parser-qa-extension.json")
    require(
        base_extension["files"] == report["protocol"]["parser_code_hashes"],
        "FIRST_PASS_PARSER_OR_CLASSIFIER_CHANGE_FORBIDDEN",
    )
    for name, expected in base_extension["files"].items():
        require(
            workflow.sec.file_sha(ROOT / name) == expected,
            "FIRST_PASS_PARSER_OR_CLASSIFIER_CHANGE_FORBIDDEN",
        )
    return qa


def case_id(row: dict[str, Any]) -> str:
    return digest(
        canonical(
            [
                "corporate-event-alpha-v1",
                "human-development-review-v1",
                EXPECTED_SHA,
                *[row[k] for k in CELL_KEYS],
                row["source_id"],
            ]
        )
    )


def batches(qa: dict[str, Any]) -> list[dict[str, Any]]:
    ordered = sorted(
        enumerate(qa["selected"]),
        key=lambda item: (item[1]["role"] != "INDEPENDENT_SOURCE_CONTROL", item[0]),
    )
    result = []
    for index, (position, row) in enumerate(ordered):
        result.append(
            {
                "batch_id": f"batch-{index // 25 + 1:03d}",
                "sequence": index + 1,
                "frozen_selection_index": position,
                "sample_record_id": case_id(row),
                "qa_cell_identity": [row[k] for k in CELL_KEYS],
                **{
                    k: row[k]
                    for k in (
                        *CELL_KEYS,
                        "source_id",
                        "source_document_sha256",
                        "source_evidence_id",
                    )
                },
            }
        )
    return result


def valid_reviewer(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{2,63}", value)) and value.lower() not in {
        "ai",
        "codex",
        "automation",
        "parser",
        "model",
        "system",
        "assistant",
        "gpt",
    }


def labels() -> dict[str, list[str]]:
    p = tomllib.loads((PROTOCOL / "event-data-protocol.toml").read_text("utf-8"))
    result = {
        "SHARE_REPURCHASE": p["repurchases"]["eligible_classes"]
        + p["repurchases"]["context_classes"],
        "INSIDER_OPEN_MARKET_PURCHASE": p["form4"]["venue_states"]
        + p["form4"]["discretion_states"]
        + p["form4"]["instrument_states"]
        + ["AMENDMENT_CORRECTION", "REJECTED_NONQUALIFYING"],
        "ACTIVIST_13D": p["activist13d"]["intent_states"],
        "DIVIDEND_POSITIVE_CHANGE": p["dividends"]["positive_subtypes"]
        + p["dividends"]["context_states"],
        "SPINOFF_ANNOUNCEMENT": p["spinoffs"]["states"],
    }
    return {
        key: sorted(set(value) | {"NO_EVENT", "UNRESOLVED", "EVENT_PRESENT_MULTIPLE_STATES"})
        for key, value in result.items()
    }


def prepare(reviewer: str, qa: dict[str, Any]) -> dict[str, Any]:
    require(valid_reviewer(reviewer), "HUMAN_SUPPLIED_PSEUDONYMOUS_REVIEWER_ID_REQUIRED")
    config = {
        "reviewer_id": reviewer,
        "human_supplied": True,
        "sample_sha256": EXPECTED_SHA,
        "run_id": RUN_ID,
        "scope": "DEVELOPMENT_FIRST_PASS_HUMAN_ONLY",
    }
    if (ADMIN / "reviewer-config.json").exists():
        require(read(ADMIN / "reviewer-config.json") == config, "REVIEWER_CONFIGURATION_CONFLICT")
    else:
        write_new(ADMIN / "reviewer-config.json", config)
    frame = {
        "sample_sha256": EXPECTED_SHA,
        "records_expected": EXPECTED_RECORDS,
        "qa_cells_expected": EXPECTED_CELLS,
        "order_rule": ORDER_RULE,
        "records": batches(qa),
    }
    if (ADMIN / "batch-manifest.json").exists():
        require(read(ADMIN / "batch-manifest.json") == frame, "IMMUTABLE_REVIEW_BATCH_MISMATCH")
    else:
        write_new(ADMIN / "batch-manifest.json", frame)
        write_new(
            ADMIN / "batch-binding.json",
            {"manifest_sha256": digest(canonical(frame)), "sample_sha256": EXPECTED_SHA},
        )
    for name in ["completion-ledger.jsonl", "development-review-issues.jsonl"]:
        if not (ADMIN / name).exists():
            workflow.sec.write_new(ADMIN / name, b"")
    (ADMIN / "inbox").mkdir(parents=True, exist_ok=True)
    return frame


def frame_and_config(qa: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    config = read(ADMIN / "reviewer-config.json")
    frame = read(ADMIN / "batch-manifest.json")
    binding = read(ADMIN / "batch-binding.json")
    require(
        binding["manifest_sha256"] == digest(canonical(frame))
        and binding["sample_sha256"] == EXPECTED_SHA,
        "IMMUTABLE_REVIEW_BATCH_MISMATCH",
    )
    require(
        frame["records"] == batches(qa)
        and config["sample_sha256"] == EXPECTED_SHA
        and config["run_id"] == RUN_ID
        and config["human_supplied"] is True
        and valid_reviewer(config["reviewer_id"]),
        "REVIEW_FRAME_OR_REVIEWER_MISMATCH",
    )
    return frame, config


def known_record(frame: dict[str, Any], record_id: str) -> dict[str, Any]:
    result = next((x for x in frame["records"] if x["sample_record_id"] == record_id), None)
    require(result is not None, "SAMPLE_RECORD_NOT_IN_FROZEN_SELECTION")
    assert result is not None
    return dict(result)


def history_state(
    frame: dict[str, Any], config: dict[str, Any]
) -> tuple[
    dict[tuple[str, str, str], dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]
]:
    known = {sample_key(row): row for row in frame["records"]}
    history = journal(RUN / "fact-review-history.jsonl")
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    review_by_id = {}
    previous = None
    for row in history:
        require(
            row["previous_record_sha256"] == previous and row["review_id"] not in review_by_id,
            "REGISTERED_REVIEW_CHAIN_INVALID",
        )
        require(
            sample_key(row) in known and row["reviewer_id"] == config["reviewer_id"],
            "UNREGISTERED_REVIEW_IDENTITY",
        )
        old = latest.get(sample_key(row))
        require(
            (old is None and row["supersedes_review_id"] is None)
            or (old is not None and row["supersedes_review_id"] == old["review_id"]),
            "REVIEW_SUPERSESSION_MUST_REFERENCE_LATEST",
        )
        latest[sample_key(row)] = row
        review_by_id[row["review_id"]] = row
        previous = row["review_id"]
    receipts = journal(ADMIN / "completion-ledger.jsonl")
    last_receipt: dict[str, dict[str, Any]] = {}
    previous_receipt = None
    for receipt in receipts:
        saved = dict(receipt)
        receipt_sha = saved.pop("journal_record_sha256")
        require(
            receipt_sha == digest(canonical(saved))
            and receipt["previous_completion_sha256"] == previous_receipt,
            "COMPLETION_LEDGER_CHAIN_INVALID",
        )
        require(receipt["review_id"] in review_by_id, "COMPLETION_REVIEW_MISSING")
        original = review_by_id[receipt["review_id"]]
        case = known[sample_key(original)]
        require(
            receipt["sample_record_id"] == case["sample_record_id"]
            and receipt["qa_cell_identity"] == case["qa_cell_identity"]
            and receipt["batch_id"] == case["batch_id"]
            and receipt["review_role"] == case["role"]
            and receipt["reviewer_id"] == config["reviewer_id"]
            and receipt["review_status"] in TERMINAL,
            "COMPLETION_IDENTITY_INVALID",
        )
        require(
            digest((RUN / "review-inbox" / receipt["review_file"]).read_bytes())
            == original["input_sha256"],
            "REGISTERED_REVIEW_INPUT_CHANGED",
        )
        require(
            digest((RUN / "packets" / (case["source_id"] + ".json")).read_bytes())
            == original["packet_sha256"],
            "REGISTERED_REVIEW_PACKET_CHANGED",
        )
        tracking = read(ADMIN / "submissions" / receipt["submission_file"])
        require(
            digest(canonical(tracking)) == receipt["submission_sha256"], "HUMAN_SUBMISSION_CHANGED"
        )
        last_receipt[receipt["sample_record_id"]] = receipt
        previous_receipt = receipt_sha
    active = {
        key: value
        for key, value in last_receipt.items()
        if latest[sample_key(known_record(frame, key))]["review_id"] == value["review_id"]
    }
    return latest, active, receipts


def all_controls_complete(frame: dict[str, Any], active: dict[str, dict[str, Any]]) -> bool:
    return all(
        x["sample_record_id"] in active
        for x in frame["records"]
        if x["role"] == "INDEPENDENT_SOURCE_CONTROL"
    )


def packet_for(case: dict[str, Any]) -> dict[str, Any]:
    name = case["source_id"] + ".json"
    raw = (RUN / "packets" / name).read_bytes()
    manifest = read(RUN / "run-manifest.json")
    require(
        digest(raw) == manifest["review_packet_hashes"][name], "FROZEN_REVIEW_PACKET_HASH_FAILURE"
    )
    packet: dict[str, Any] = json.loads(raw)
    require(
        packet["source_id"] == case["source_id"]
        and packet["source_document_sha256"] == case["source_document_sha256"]
        and packet["source_evidence_id"] == case["source_evidence_id"],
        "PACKET_SOURCE_IDENTITY_FAILURE",
    )
    return packet


def registered_evidence() -> dict[str, tuple[str, str, str, str]]:
    path = RUN / "development-events.sqlite3"
    require(
        workflow.sec.file_sha(path) == read(RUN / "run-manifest.json")["files"][path.name],
        "FROZEN_EVIDENCE_DATABASE_CHANGED",
    )
    with sqlite3.connect("file:" + path.as_posix() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        return {
            row[0]: tuple(row[1:])
            for row in db.execute(
                "SELECT evidence_id,disclosure_id,object_sha256,object_locator,evidence_locator "
                "FROM source_evidence"
            )
        }


def validate_record(
    record: dict[str, Any],
    case: dict[str, Any],
    reviewer: str,
    packet: dict[str, Any],
    evidence: dict[str, tuple[str, str, str, str]],
    vocab: dict[str, list[str]],
    previous: dict[str, Any] | None,
) -> None:
    require(
        set(record)
        == {"sample_record_id", "review", "first_pass_status", "concerns", "human_attested"},
        "UNKNOWN_OR_MISSING_SUBMISSION_RECORD_FIELD",
    )
    require(
        record["sample_record_id"] == case["sample_record_id"] and record["human_attested"] is True,
        "EXACT_SAMPLE_ID_AND_HUMAN_ATTESTATION_REQUIRED",
    )
    item = record["review"]
    require(isinstance(item, dict) and set(item) == REVIEW_KEYS, "UNKNOWN_OR_MISSING_REVIEW_FIELD")
    require(
        sample_key(item) == sample_key(case) and item["reviewer_id"] == reviewer,
        "REVIEWER_SOURCE_FAMILY_OR_ROLE_MISMATCH",
    )
    require(item["scope"] == "DEVELOPMENT_FACT_REVIEW_ONLY", "REVIEW_SCOPE_INVALID")
    require(
        item["decision"] in {"ACCEPTED", "REJECTED", "AMBIGUOUS"}, "CONTROLLED_DECISION_INVALID"
    )
    require(
        isinstance(item["reason"], str) and bool(item["reason"].strip()), "HUMAN_REASON_REQUIRED"
    )
    require(
        isinstance(item["correct_state"], str) and item["correct_state"] in vocab[case["family"]],
        "FAMILY_CONTROLLED_STATE_INVALID",
    )
    require(
        item["event_presence"] is None or isinstance(item["event_presence"], bool),
        "EVENT_PRESENCE_MUST_BE_BOOL_OR_NULL",
    )
    proposals = packet["candidate_proposals"]
    if case["role"] == "INDEPENDENT_SOURCE_CONTROL":
        require(item["event_id"] is None, "CONTROL_MUST_NOT_REFERENCE_MACHINE_EVENT")
    else:
        require(
            any(
                p["event_id"] == item["event_id"] and p["family"] == case["family"]
                for p in proposals
            ),
            "CANDIDATE_EVENT_AND_FAMILY_MISMATCH",
        )
    ids = item["evidence_ids"]
    require(
        isinstance(ids, list)
        and bool(ids)
        and all(isinstance(x, str) for x in ids)
        and len(set(ids)) == len(ids),
        "NONEMPTY_UNIQUE_EVIDENCE_IDS_REQUIRED",
    )
    permitted = {packet["source_evidence_id"]} | {p["evidence"]["evidence_id"] for p in proposals}
    require(set(ids) <= permitted, "EVIDENCE_NOT_IN_PACKET")
    for evidence_id in ids:
        require(
            evidence_id in evidence
            and evidence[evidence_id][0] == case["source_id"]
            and evidence[evidence_id][1] == packet["container_sha256"]
            and evidence[evidence_id][2] == packet["container_locator"]
            and bool(evidence[evidence_id][3]),
            "EVIDENCE_NOT_REGISTERED_FOR_SOURCE",
        )
    require(record["first_pass_status"] in TERMINAL, "FIRST_PASS_TERMINAL_STATUS_REQUIRED")
    concerns = record["concerns"]
    require(isinstance(concerns, list), "CONCERNS_MUST_BE_EXPLICIT_LIST")
    seen = set()
    for concern in concerns:
        require(
            isinstance(concern, dict)
            and set(concern) == {"category", "description", "severity_candidate", "reviewer_note"},
            "CONCERN_FIELDS_INVALID",
        )
        require(
            concern["category"] in TAXONOMY and concern["category"] not in seen,
            "CONCERN_CATEGORY_INVALID_OR_DUPLICATED",
        )
        require(
            isinstance(concern["description"], str)
            and bool(concern["description"].strip())
            and isinstance(concern["reviewer_note"], str),
            "HUMAN_ISSUE_DESCRIPTION_REQUIRED",
        )
        require(
            concern["severity_candidate"] in {None, "LOW", "MEDIUM", "HIGH", "CRITICAL"},
            "ISSUE_SEVERITY_INVALID",
        )
        seen.add(concern["category"])
    if previous is None:
        require(item["supersedes_review_id"] is None, "SUPERSESSION_WITHOUT_PRIOR_REVIEW")
    elif all(item[k] == previous[k] for k in REVIEW_KEYS):
        require(
            previous["input_sha256"] == digest(canonical(item)), "IDEMPOTENT_REVIEW_INPUT_MISMATCH"
        )
    else:
        require(
            item["supersedes_review_id"] == previous["review_id"],
            "SUPERSESSION_MUST_REFERENCE_LATEST_REVIEW",
        )


def validate_submission(
    submission: dict[str, Any],
    frame: dict[str, Any],
    config: dict[str, Any],
    latest: dict[tuple[str, str, str], dict[str, Any]],
    active: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    require(
        set(submission) == {"version", "batch_id", "reviewer_id", "records"}
        and submission["version"] == "development-human-submission-v1",
        "SUBMISSION_FIELDS_OR_VERSION_INVALID",
    )
    require(submission["reviewer_id"] == config["reviewer_id"], "HUMAN_REVIEWER_ID_MISMATCH")
    batch_id = submission["batch_id"]
    require(batch_id in {row["batch_id"] for row in frame["records"]}, "BATCH_NOT_REGISTERED")
    records = submission["records"]
    require(isinstance(records, list) and 1 <= len(records) <= 25, "REVIEW_SUBMISSION_SIZE_INVALID")
    require(
        all(isinstance(x, dict) and isinstance(x.get("sample_record_id"), str) for x in records),
        "REVIEW_RECORD_ID_REQUIRED",
    )
    require(
        len({x["sample_record_id"] for x in records}) == len(records), "DUPLICATE_REVIEW_RECORD"
    )
    cases = [known_record(frame, x["sample_record_id"]) for x in records]
    require(all(x["batch_id"] == batch_id for x in cases), "RECORD_NOT_IN_SUBMITTED_BATCH")
    require(
        all(x["role"] == "INDEPENDENT_SOURCE_CONTROL" for x in cases)
        or all_controls_complete(frame, active),
        "CONTROL_FIRST_BARRIER_CLOSED",
    )
    vocab = labels()
    evidence = registered_evidence()
    for record, case in zip(records, cases, strict=True):
        validate_record(
            record,
            case,
            config["reviewer_id"],
            packet_for(case),
            evidence,
            vocab,
            latest.get(sample_key(case)),
        )
    return cases


def invoke_registered(input_name: str) -> dict[str, Any]:
    command = [
        sys.executable,
        "-I",
        "-S",
        "-B",
        str(ROOT / "tools/corporate_events/parser_qa.py"),
        "record-review",
        "--run",
        RUN_ID,
        "--review-input",
        input_name,
    ]
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    require(done.returncode == 0, "REGISTERED_REVIEW_COMMAND_REJECTED_INPUT")
    result: dict[str, Any] = json.loads(done.stdout)
    require(result["status"] == "FACT_REVIEW_APPENDED", "REGISTERED_REVIEW_COMMAND_FAILED")
    return result


def append_receipt(value: dict[str, Any]) -> None:
    old = journal(ADMIN / "completion-ledger.jsonl")
    value = {
        **value,
        "previous_completion_sha256": old[-1]["journal_record_sha256"] if old else None,
    }
    value["journal_record_sha256"] = digest(canonical(value))
    workflow.sec.append_record(ADMIN / "completion-ledger.jsonl", value)


def record_submission(name: str, qa: dict[str, Any]) -> dict[str, Any]:
    require(
        bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\.json", name)),
        "LOCAL_INBOX_JSON_FILENAME_REQUIRED",
    )
    frame, config = frame_and_config(qa)
    submission = read(ADMIN / "inbox" / name)
    latest, active, _ = history_state(frame, config)
    cases = validate_submission(submission, frame, config, latest, active)
    submission_sha = digest(canonical(submission))
    submission_name = submission_sha + ".json"
    archive = ADMIN / "submissions" / submission_name
    if archive.exists():
        require(archive.read_bytes() == canonical(submission), "IMMUTABLE_SUBMISSION_CONFLICT")
    else:
        write_new(archive, submission)
    appended = 0
    for item, case in zip(submission["records"], cases, strict=True):
        transaction_id = digest(canonical([submission_sha, case["sample_record_id"]]))
        old_receipts = journal(ADMIN / "completion-ledger.jsonl")
        if any(x["transaction_id"] == transaction_id for x in old_receipts):
            continue
        review = item["review"]
        input_name = "human-" + digest(canonical(review)) + ".json"
        destination = RUN / "review-inbox" / input_name
        if destination.exists():
            require(
                destination.read_bytes() == canonical(review), "IMMUTABLE_REVIEW_INPUT_CONFLICT"
            )
        else:
            write_new(destination, review)
        before = journal(RUN / "fact-review-history.jsonl")
        previous = next((x for x in reversed(before) if sample_key(x) == sample_key(case)), None)
        if previous is not None and all(previous[k] == review[k] for k in REVIEW_KEYS):
            actual = previous
        else:
            result = invoke_registered(input_name)
            after = journal(RUN / "fact-review-history.jsonl")
            require(
                len(after) == len(before) + 1
                and after[-1]["review_id"] == result["review_id"]
                and all(after[-1][k] == review[k] for k in REVIEW_KEYS),
                "REGISTERED_REVIEW_APPEND_VERIFICATION_FAILED",
            )
            actual = after[-1]
            appended += 1
        issues = journal(ADMIN / "development-review-issues.jsonl")
        for concern in item["concerns"]:
            issue_id = digest(canonical([transaction_id, concern["category"]]))
            if any(x["issue_id"] == issue_id for x in issues):
                continue
            workflow.sec.append_record(
                ADMIN / "development-review-issues.jsonl",
                {
                    "issue_id": issue_id,
                    "first_seen_record": case["sample_record_id"],
                    "family": case["family"],
                    "issue_category": concern["category"],
                    "description": concern["description"],
                    "affected_record_ids_known_so_far": [case["sample_record_id"]],
                    "severity_candidate": concern["severity_candidate"],
                    "reviewer_note": concern["reviewer_note"],
                    "status": "OPEN_FIRST_PASS",
                    "reviewer_id": config["reviewer_id"],
                    "review_id": actual["review_id"],
                    "transaction_id": transaction_id,
                },
            )
        append_receipt(
            {
                "transaction_id": transaction_id,
                "batch_id": case["batch_id"],
                "sample_record_id": case["sample_record_id"],
                "qa_cell_identity": case["qa_cell_identity"],
                "review_role": case["role"],
                "review_status": item["first_pass_status"],
                "review_file": input_name,
                "recorded_at": actual["reviewed_at_utc"],
                "reviewer_id": config["reviewer_id"],
                "review_id": actual["review_id"],
                "submission_file": submission_name,
                "submission_sha256": submission_sha,
            }
        )
    return {
        "status": "HUMAN_SUBMISSION_RECORDED",
        "substantive_reviews_appended": appended,
        "records_submitted": len(cases),
        "batch_id": submission["batch_id"],
    }


def tracking_for(receipt: dict[str, Any]) -> dict[str, Any]:
    submission = read(ADMIN / "submissions" / receipt["submission_file"])
    return next(
        x for x in submission["records"] if x["sample_record_id"] == receipt["sample_record_id"]
    )


def progress(frame: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    latest, active, receipts = history_state(frame, config)
    statuses = Counter(x["review_status"] for x in active.values())
    findings = {key: tracking_for(value) for key, value in active.items()}
    taxonomy = Counter(c["category"] for record in findings.values() for c in record["concerns"])

    def distribution(fields: tuple[str, ...]) -> list[dict[str, Any]]:
        counts: dict[tuple[str, ...], Counter[str]] = {}
        for row in frame["records"]:
            key = tuple(row[k] for k in fields)
            count = counts.setdefault(key, Counter())
            count["expected"] += 1
            if row["sample_record_id"] in active:
                count["reviewed"] += 1
        return [
            {
                **dict(zip(fields, key, strict=True)),
                **dict(value),
                "reviewed": value["reviewed"],
                "remaining": value["expected"] - value["reviewed"],
            }
            for key, value in sorted(counts.items())
        ]

    def concern_count(categories: set[str]) -> int:
        return sum(
            bool(categories & {x["category"] for x in item["concerns"]})
            for item in findings.values()
        )

    all_done = len(active) == EXPECTED_RECORDS
    result = {
        "status": "FIRST_PASS_REVIEW_COMPLETE_DIAGNOSIS_PENDING"
        if all_done
        else "B_DEVELOPMENT_HUMAN_REVIEW_INCOMPLETE",
        "sample_sha256": EXPECTED_SHA,
        "records_expected": EXPECTED_RECORDS,
        "qa_cells_expected": EXPECTED_CELLS,
        "reviewer_id": config["reviewer_id"],
        "batches_created": len({x["batch_id"] for x in frame["records"]}),
        "batch_size": 25,
        "order_rule": ORDER_RULE,
        "reviewed": len(active),
        "remaining": EXPECTED_RECORDS - len(active),
        "human_first_pass_complete": all_done,
        "terminal_states": dict(statuses),
        "reviewed_accepted": sum(
            latest[sample_key(known_record(frame, key))]["decision"] == "ACCEPTED" for key in active
        ),
        "requires_adjudication": statuses["REVIEWED_REQUIRES_ADJUDICATION"],
        "unresolved": statuses["REVIEWED_UNRESOLVED"],
        "untracked_registered_reviews": sum(
            case["sample_record_id"] not in active
            for case in frame["records"]
            if sample_key(case) in latest
        ),
        "extraction_concerns": concern_count(
            {"FACT_EXTRACTION", "STRUCTURAL_PARSE", "CONTAINER_RESOLUTION", "DOCUMENT_SELECTION"}
        ),
        "family_classification_concerns": concern_count({"FAMILY_CLASSIFICATION"}),
        "controlled_state_concerns": concern_count({"CONTROLLED_STATE"}),
        "evidence_locator_concerns": concern_count({"EVIDENCE_LOCATOR"}),
        "dedup_action_anchor_concerns": concern_count({"DEDUP_ACTION_ANCHOR"}),
        "timing_concerns": concern_count({"TIMESTAMP_AUTHORITY"}),
        "counts_by_family": distribution(("family",)),
        "counts_by_role": distribution(("role",)),
        "counts_by_era": distribution(("era",)),
        "counts_by_form_source": distribution(("form", "source_kind")),
        "counts_by_batch": distribution(("batch_id",)),
        "error_taxonomy_counts": {x: taxonomy[x] for x in TAXONOMY},
        "systematic_issues_identified": "NOT_ASSESSED_FIRST_PASS_INCOMPLETE"
        if not all_done
        else "HUMAN_REPORTED_ISSUES_ONLY",
        "proposed_future_fixes": "NOT_PROPOSED_FIRST_PASS_INCOMPLETE"
        if not all_done
        else "HUMAN_ISSUE_DIAGNOSIS_ONLY_NO_AUTOMATIC_PATCH",
        "parser_changes_during_first_pass": 0,
        "classifier_changes_during_first_pass": 0,
        "qa_redraws": 0,
        "qa_replacements": 0,
        "new_live_filings": 0,
        "network_requests": 0,
        "firewall_counters": dict.fromkeys(workflow.sec.FIREWALL_COUNTERS, 0),
        "full_extraction_authorized": False,
        "final_qa_authorized": False,
        "phase3_authorized": False,
        "human_submission_records": len(receipts),
    }
    metrics: dict[str, dict[str, Any]] = {
        name: {"status": "NOT_CALCULATED_FIRST_PASS_INCOMPLETE"}
        for name in (
            "machine_proposal_agreement",
            "fact_extraction_correctness",
            "family_agreement",
            "controlled_state_agreement",
            "evidence_locator_correctness",
            "ambiguity_unresolved_rate",
            "review_adjudication_rate",
            "dedup_concern_rate",
            "timing_concern_rate",
            "recall",
        )
    }
    if all_done:
        for name, count in [
            ("ambiguity_unresolved_rate", result["unresolved"]),
            ("review_adjudication_rate", result["requires_adjudication"]),
            ("dedup_concern_rate", result["dedup_action_anchor_concerns"]),
            ("timing_concern_rate", result["timing_concerns"]),
        ]:
            metrics[name] = {
                "status": "MEASURED_DOCUMENT_REVIEW_RATE",
                "numerator": count,
                "denominator": EXPECTED_RECORDS,
                "value": count / EXPECTED_RECORDS,
            }
        for name in set(metrics) - {
            "ambiguity_unresolved_rate",
            "review_adjudication_rate",
            "dedup_concern_rate",
            "timing_concern_rate",
        }:
            metrics[name] = {
                "status": "NOT_MEASURED",
                "reason": "Existing review schema/design does not independent"
                "ly label this metric; no inference from missing co"
                "ncerns.",
            }
        agreed = eligible = 0
        for key, receipt in active.items():
            case = known_record(frame, key)
            human = latest[sample_key(case)]
            if (
                case["role"] != "PARSER_CANDIDATE"
                or receipt["review_status"] != "REVIEWED"
                or human["correct_state"] in {"UNRESOLVED", "EVENT_PRESENT_MULTIPLE_STATES"}
            ):
                continue
            proposal = next(
                x
                for x in packet_for(case)["candidate_proposals"]
                if x["event_id"] == human["event_id"]
            )
            eligible += 1
            agreed += human["correct_state"] == proposal["proposed_state"]
        metrics["controlled_state_agreement"] = {
            "status": "MEASURED_CHOSEN_PROPOSAL_STATE_AGREEMENT"
            if eligible
            else "NOT_MEASURED_EMPTY_DENOMINATOR",
            "numerator": agreed,
            "denominator": eligible,
            "value": agreed / eligible if eligible else None,
            "unit": "One human-chosen proposal per resolved candidate d"
            "ocument-family record; development sample only, no"
            "t precision or final QA.",
        }
    result["metrics"] = metrics
    result["metric_policy"] = (
        "No final or acceptance metrics before all frozen records have human "
        "terminal dispositions. Missing concern flags never"
        " imply correctness; recall is not supported."
    )
    (ADMIN / "progress.json").write_bytes(canonical(result))
    queue = []
    for key, item in findings.items():
        case = known_record(frame, key)
        for concern in item["concerns"]:
            queue.append(
                {
                    "sample_record_id": key,
                    "batch_id": case["batch_id"],
                    "family": case["family"],
                    "review_id": active[key]["review_id"],
                    **concern,
                    "status": "OPEN_FIRST_PASS",
                }
            )
    (ADMIN / "discrepancy-queue.json").write_bytes(canonical(queue))
    write_status_report(result)
    return result


def write_status_report(value: dict[str, Any]) -> None:
    lines = [
        "# Phase 2C human development review",
        "",
        value["status"],
        "",
        f"Reviewer: `{value['reviewer_id']}`. Frozen sample: `{EXPECTED_SHA}`.",
        "",
        f"Reviewed **{value['reviewed']} / {EXPECTED_RECORDS}**; "
        f"remaining **{value['remaining']}**. "
        f"Batches: {value['batches_created']} (25 records; final 18).",
        "",
        ORDER_RULE,
        "",
        "Candidate views remain locked until every source c"
        "ontrol has a recorded human disposition.",
        "",
        f"Accepted: {value['reviewed_accepted']}; adjudication: {value['requires_adjudication']}; "
        f"unresolved: {value['unresolved']}.",
        "",
        "| Family | Expected | Reviewed | Remaining |",
        "|---|---:|---:|---:|",
    ]
    lines += [
        f"| {x['family']} | {x['expected']} | {x['reviewed']} | {x['remaining']} |"
        for x in value["counts_by_family"]
    ]
    lines += ["", "| Role | Expected | Reviewed | Remaining |", "|---|---:|---:|---:|"]
    lines += [
        f"| {x['role']} | {x['expected']} | {x['reviewed']} | {x['remaining']} |"
        for x in value["counts_by_role"]
    ]
    lines += [
        "",
        "## Human-identified concerns",
        "",
        *[
            f"- {name}: {value[name]}"
            for name in (
                "extraction_concerns",
                "family_classification_concerns",
                "controlled_state_concerns",
                "evidence_locator_concerns",
                "dedup_action_anchor_concerns",
                "timing_concerns",
            )
        ],
        "",
        "## Metrics and diagnosis",
        "",
        value["metric_policy"],
        "",
        *[f"- {key}: {item['status']}" for key, item in value["metrics"].items()],
        "",
        f"Systematic issues: {value['systematic_issues_identified']}.",
        f"Future fixes: {value['proposed_future_fixes']}.",
        "",
        "No parser/classifier changes, sample redraws, replacements, new filings or networking. "
        "Human decisions and reasons are not inferred. Concern counts are human-reported flags, "
        "not independently measured error rates.",
        "",
        "Historical XSD/schema features, dissemination timing, history completeness, baselines, "
        "repurchase chronology and earliest spin-off disclosure remain open authority tasks.",
        "",
        "Full extraction, final QA and Phase 3 remain unauthorized.",
        "",
        "## Outcome firewall",
        "",
        "| Counter | Count |",
        "|---|---:|",
        *[f"| {name} | {count} |" for name, count in value["firewall_counters"].items()],
        "",
    ]
    (ADMIN / "first-pass-status.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


FORM_SCRIPT = r"""
const config=JSON.parse(document.getElementById('config').textContent);
document.getElementById('export').onclick=()=>{
 try {
  const records=[];
  for(const card of document.querySelectorAll('.case')){
   const get=n=>card.querySelector('[name="'+n+'"]');
   const populated=[...card.querySelectorAll('select,textarea')].some(x=>x.value!=='') ||
     [...card.querySelectorAll('[name="concern"]')].some(x=>x.checked);
   if(!get('attest').checked){
    if(populated)throw Error('Confirm your review for '+card.dataset.id);continue;}
   for(const name of ['decision','correct_state','presence','first_pass_status','reason'])
    if(!get(name).value.trim())throw Error('Complete '+name+' for '+card.dataset.id);
   const evidence=[...get('evidence').selectedOptions].map(x=>x.value);
   if(!evidence.length)throw Error('Select source evidence for '+card.dataset.id);
   const categories=[...card.querySelectorAll('[name="concern"]')]
    .filter(x=>x.checked).map(x=>x.value);
   if(categories.length && !get('issue_description').value.trim())
    throw Error('Describe the concerns.');
   const event=card.dataset.role==='PARSER_CANDIDATE'?get('event_id').value:null;
   if(card.dataset.role==='PARSER_CANDIDATE'&&!event)
    throw Error('Choose the reviewed proposal event ID.');
   records.push({sample_record_id:card.dataset.id,human_attested:true,
    first_pass_status:get('first_pass_status').value,
    concerns:categories.map(category=>({category,description:get('issue_description').value,
      severity_candidate:get('severity').value||null,reviewer_note:get('reviewer_note').value})),
    review:{source_id:card.dataset.source,family:card.dataset.family,role:card.dataset.role,
      reviewer_id:config.reviewer_id,decision:get('decision').value,reason:get('reason').value,
      event_id:event,evidence_ids:evidence,supersedes_review_id:get('supersedes').value.trim()||null,
      scope:'DEVELOPMENT_FACT_REVIEW_ONLY',correct_state:get('correct_state').value,
      event_presence:get('presence').value==='UNKNOWN'?null:get('presence').value==='YES'}});
  }
  if(!records.length)throw Error('No completed human reviews to export.');
  const submission={version:'development-human-submission-v1',batch_id:config.batch_id,
      reviewer_id:config.reviewer_id,records};
  const link=document.createElement('a');
  link.href=URL.createObjectURL(new Blob([JSON.stringify(submission,null,2)+'\n'],
    {type:'application/json'}));
  link.download=config.batch_id+'-'+config.reviewer_id+'.json';link.click();
  document.getElementById('message').textContent='Exported '+records.length+
   ' human reviews. Save the JSON in the private inbox, then run the record command. '+
   'No local record has been appended by this page.';
 } catch(e){document.getElementById('message').textContent=e.message;}
};
"""


def select_control(name: str, values: list[str]) -> str:
    options = "".join(
        '<option value="' + html.escape(x, quote=True) + '">' + html.escape(x) + "</option>"
        for x in values
    )
    return (
        '<select name="'
        + name
        + '"><option value="">Choose explicitly</option>'
        + options
        + "</select>"
    )


def render_views(frame: dict[str, Any], config: dict[str, Any]) -> None:
    _, active, _ = history_state(frame, config)
    unlocked = all_controls_complete(frame, active)
    vocab = labels()
    packet_manifest = read(RUN / "run-manifest.json")["review_packet_hashes"]
    (ADMIN / "batches").mkdir(exist_ok=True)
    links = []
    for batch_id in dict.fromkeys(x["batch_id"] for x in frame["records"]):
        members = [x for x in frame["records"] if x["batch_id"] == batch_id]
        visible = [x for x in members if unlocked or x["role"] == "INDEPENDENT_SOURCE_CONTROL"]
        pending = sum(x["sample_record_id"] not in active for x in members)
        if not visible:
            links.append(f"<li>{batch_id}: locked until all source controls are reviewed</li>")
            continue
        cards = []
        for case in visible:
            candidate = case["role"] == "PARSER_CANDIDATE"
            suffix = ".html" if candidate else ".control.html"
            page_name = case["source_id"] + suffix
            require(
                workflow.sec.file_sha(RUN / "packets" / page_name) == packet_manifest[page_name],
                "FROZEN_REVIEW_PAGE_HASH_FAILURE",
            )
            packet = packet_for(case) if candidate else None
            evidence_options = [case["source_evidence_id"]]
            event_options: list[str] = []
            if packet is not None:
                proposals = [
                    x for x in packet["candidate_proposals"] if x["family"] == case["family"]
                ]
                evidence_options += sorted({x["evidence"]["evidence_id"] for x in proposals})
                event_options = [x["event_id"] for x in proposals]
            choices = "".join(
                '<option value="' + x + '">' + x + "</option>" for x in evidence_options
            )
            concern_options = "".join(
                '<label><input type="checkbox" name="concern" value="' + x + '">' + x + "</label> "
                for x in TAXONOMY
            )
            identity = html.escape(case["sample_record_id"])
            supersedes = active[identity]["review_id"] if identity in active else ""
            card = (
                '<section class="case" data-id="'
                + identity
                + '" data-source="'
                + case["source_id"]
                + '" data-family="'
                + case["family"]
                + '" data-role="'
                + case["role"]
                + '">'
                + "<h2>Record "
                + str(case["sequence"])
                + " | "
                + html.escape(case["family"])
                + "</h2>"
                + "<p>"
                + html.escape(case["era"] + " | " + case["form"] + " | " + case["source_kind"])
                + "</p>"
                + "<p>Sample record: <code>"
                + identity
                + "</code></p>"
                + '<p><a target="_blank" rel="noopener" href="../../runs/'
                + RUN_ID
                + "/packets/"
                + case["source_id"]
                + suffix
                + '">Open '
                + ("machine proposal view" if candidate else "source-only control view")
                + "</a></p>"
                + "<p>A source control is not assumed negative. Review source evidence yourself. "
                "Acceptance, filing and printed dates do not certify dissemination.</p>"
                + "<label>Human decision "
                + select_control("decision", ["ACCEPTED", "REJECTED", "AMBIGUOUS"])
                + "</label>"
                + "<label>Controlled state "
                + select_control("correct_state", vocab[case["family"]])
                + "</label>"
                + "<label>Family evidence/event present? "
                + select_control("presence", ["YES", "NO", "UNKNOWN"])
                + "</label>"
                + "<label>First-pass disposition "
                + select_control("first_pass_status", sorted(TERMINAL))
                + "</label>"
                + (
                    (
                        "<label>Proposal event ID "
                        + select_control("event_id", event_options)
                        + "</label>"
                    )
                    if candidate
                    else ""
                )
                + "<label>Evidence IDs (select at least one; whole-source evidence is available)"
                + '<select multiple name="evidence">'
                + choices
                + "</select></label>"
                + '<label>Reason <textarea name="reason"></textarea></label>'
                + "<fieldset><legend>Human-identified concerns</legend>"
                + concern_options
                + "</fieldset>"
                + "<label>Description for selected concerns <textarea"
                ' name="issue_description"></textarea></label>'
                + "<label>Severity candidate "
                + select_control("severity", ["LOW", "MEDIUM", "HIGH", "CRITICAL"])
                + "</label>"
                + '<label>Reviewer note <textarea name="reviewer_note"></textarea></label>'
                + '<label>Supersedes review ID <input name="supersedes" value="'
                + supersedes
                + '"></label>'
                + '<label><input type="checkbox" name="attest">I pers'
                "onally reviewed the source, labels, evidence and c"
                "oncern selections. These are my human decisions.</"
                "label>"
                + "<p>Current administrative status: "
                + html.escape(
                    active[identity]["review_status"]
                    if identity in active
                    else "PENDING_HUMAN_REVIEW"
                )
                + "</p></section>"
            )
            cards.append(card)
        config_json = json.dumps(
            {"batch_id": batch_id, "reviewer_id": config["reviewer_id"]}
        ).replace("<", "\\u003c")
        page = (
            '<!doctype html><html><head><meta charset="utf-8">'
            + '<meta http-equiv="Content-Security-Policy" content'
            "=\"default-src 'none'; script-src 'unsafe-inline'; "
            "style-src 'unsafe-inline'; connect-src 'none'; for"
            "m-action 'none'; base-uri 'none'\">"
            + "<title>Human development review "
            + batch_id
            + "</title><style>body{max-width:1000px;margin:2rem a"
            "uto;font:16px system-ui}section{border-top:3px sol"
            "id #345;padding:2rem 0}label{display:block;margin:"
            ".7rem 0}textarea{display:block;width:95%;min-heigh"
            "t:5rem}select,input{max-width:95%}code{overflow-wr"
            "ap:anywhere}button{padding:1rem}</style></head><bo"
            "dy>"
            + "<h1>"
            + batch_id
            + " — human review</h1><p>Reviewer: "
            + html.escape(config["reviewer_id"])
            + "</p>"
            + "<p>25-record deterministic batches; final remainde"
            "r 18. Only available source-control records are sh"
            "own before the global control barrier opens. Hidde"
            "n candidate records are not linked or described.</"
            "p>" + "<p>No decision is prefilled. Exported JSON is not "
            "recorded until validated by the administrative com"
            "mand.</p>" + "".join(cards) + '<button id="export">Export completed human reviews'
            '</button><p id="message" role="status"></p>'
            + '<script type="application/json" id="config">'
            + config_json
            + "</script><script>"
            + FORM_SCRIPT
            + "</script></body></html>"
        )
        (ADMIN / "batches" / (batch_id + ".html")).write_text(page, encoding="utf-8", newline="\n")
        links.append(
            '<li><a href="batches/'
            + batch_id
            + '.html">'
            + batch_id
            + "</a> — "
            + str(pending)
            + " pending</li>"
        )
    index = (
        '<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" '
        + "content=\"default-src 'none'\"><h1>Frozen development human review</h1><p>Reviewer "
        + html.escape(config["reviewer_id"])
        + "; "
        + str(len(active))
        + " / "
        + str(EXPECTED_RECORDS)
        + " recorded.</p>"
        + "<p>Source-only controls precede all proposal views"
        ". Do not use the older mixed packet index during t"
        "his first pass.</p>" + "<p>Save exported review JSON in human-review-v1/in"
        "box. Then run python -I -S -B tools/corporate_even"
        "ts/review_admin.py record --input FILENAME.json. T"
        "his uses the registered record-review command.</p>"
        "<ul>" + "".join(links) + "</ul><p>Full extraction, final QA and Phase 3 remain closed.</p>"
    )
    (ADMIN / "review-index.html").write_text(index, encoding="utf-8", newline="\n")


def install_guard() -> None:
    """No network, original sources, vendor modules or arbitrary child commands."""
    original_import = builtins.__import__
    library = (Path(sys.base_prefix) / "Lib").resolve()
    allowed_sources = {
        Path(__file__).resolve(),
        ROOT / "tools/corporate_events/parser_qa.py",
        ROOT / "tools/corporate_events/event_parsers.py",
        ROOT / "tools/corporate_events/sec_document_client.py",
        ROOT / "tools/corporate_events/sec_census.py",
        ROOT / "tools/corporate_events/phase2.py",
    }

    def permitted(value: object, writing: bool = False) -> bool:
        if isinstance(value, int):
            return True  # Existing stdio and pipes for the one registered review subprocess.
        if not isinstance(value, (str, bytes, os.PathLike)):
            return False
        path = Path(os.fsdecode(value)).resolve()
        if "prospective" in {x.lower() for x in path.parts}:
            return False
        if writing:
            return (
                path == ADMIN
                or ADMIN in path.parents
                or path == RUN / "review-inbox"
                or RUN / "review-inbox" in path.parents
            )
        if path == PILOT or PILOT in path.parents:
            return not any(
                path == blocked or blocked in path.parents
                for blocked in (PILOT / "objects", RUN / "documents")
            )
        return (
            path in allowed_sources
            or path == PROTOCOL
            or PROTOCOL in path.parents
            or (library in path.parents and "site-packages" not in {x.lower() for x in path.parts})
        )

    def guarded_import(
        name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0
    ) -> Any:
        resolved = str((globals or {}).get("__package__", "")) if level else name
        require(workflow.sec.import_allowed(resolved), "REVIEW_ADMIN_IMPORT_DENIED")
        return original_import(name, globals, locals, fromlist, level)

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event.startswith("socket.") or event == "http.client.connect":
            raise PermissionError("REVIEW_ADMIN_NETWORK_DENIED")
        if event == "open":
            mode = args[1]
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            writing = (
                isinstance(mode, str)
                and any(c in mode for c in "wax+")
                or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            )
            require(permitted(args[0], writing), "REVIEW_ADMIN_FILE_DENIED")
        elif event in {"os.listdir", "os.scandir"}:
            require(permitted(args[0]), "REVIEW_ADMIN_DIRECTORY_DENIED")
        elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.truncate"}:
            require(permitted(args[0], True), "REVIEW_ADMIN_MUTATION_DENIED")
        elif event in {"os.rename", "os.replace"}:
            require(all(permitted(x, True) for x in args[:2]), "REVIEW_ADMIN_MUTATION_DENIED")
        elif event == "sqlite3.connect":
            target = str(args[0])
            require(
                target == "file:" + (RUN / "development-events.sqlite3").as_posix() + "?mode=ro",
                "REVIEW_ADMIN_DATABASE_DENIED",
            )
        elif event == "subprocess.Popen":
            command = args[1]
            expected = [
                sys.executable,
                "-I",
                "-S",
                "-B",
                str(ROOT / "tools/corporate_events/parser_qa.py"),
                "record-review",
                "--run",
                RUN_ID,
                "--review-input",
            ]
            if isinstance(command, str):
                match = re.search(r" (human-[0-9a-f]{64}\.json)$", command)
                valid = match is not None and command == subprocess.list2cmdline(
                    expected + [match[1]]
                )
            else:
                valid = (
                    isinstance(command, list)
                    and command[:-1] == expected
                    and bool(re.fullmatch(r"human-[0-9a-f]{64}\.json", command[-1]))
                )
            require(
                valid
                and (args[0] is None or Path(args[0]).resolve() == Path(sys.executable).resolve())
                and Path(args[2]).resolve() == ROOT,
                "REVIEW_ADMIN_PROCESS_DENIED",
            )
        elif event.startswith(("ctypes.", "os.exec", "os.spawn", "os.startfile")) or event in {
            "os.system",
            "os.fork",
            "os.posix_spawn",
            "sqlite3.enable_load_extension",
            "sqlite3.load_extension",
        }:
            raise PermissionError("REVIEW_ADMIN_NATIVE_DENIED")
        elif event == "exec":
            filename = args[0].co_filename
            if not filename.startswith("<frozen "):
                require(permitted(filename), "REVIEW_ADMIN_EXEC_DENIED")

    builtins.__import__ = guarded_import
    sys.addaudithook(audit)


def main() -> int:
    if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode:
        print("Use python -I -S -B tools/corporate_events/review_admin.py", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "validate", "record", "status"])
    parser.add_argument("--reviewer-id", default="")
    parser.add_argument("--input", default="")
    args = parser.parse_args()
    locked = False
    try:
        qa = verify_frozen()
        workflow.preflight()
        own = read(PROTOCOL / "review-admin-extension.json")
        require(
            set(own["files"])
            == {"tools/corporate_events/review_admin.py", "tests/test_review_admin.py"},
            "REVIEW_ADMIN_EXTENSION_SCOPE_MISMATCH",
        )
        for name, expected in own["files"].items():
            require(
                workflow.sec.file_sha(ROOT / name) == expected, "REVIEW_ADMIN_CODE_HASH_MISMATCH"
            )
        install_guard()
        ADMIN.mkdir(parents=True, exist_ok=True)
        workflow.sec.write_new(ADMIN / "review-admin.lock", canonical({"pid": os.getpid()}))
        locked = True
        if args.command == "prepare":
            frame = prepare(args.reviewer_id, qa)
            config = read(ADMIN / "reviewer-config.json")
        else:
            frame, config = frame_and_config(qa)
        if args.command in {"validate", "record"}:
            require(
                bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\.json", args.input)),
                "LOCAL_INBOX_JSON_FILENAME_REQUIRED",
            )
            if args.command == "validate":
                latest, active, _ = history_state(frame, config)
                cases = validate_submission(
                    read(ADMIN / "inbox" / args.input), frame, config, latest, active
                )
                result = {
                    "status": "HUMAN_SUBMISSION_VALID",
                    "records": len(cases),
                    "writes_to_registered_history": 0,
                }
            else:
                result = record_submission(args.input, qa)
        else:
            result = {"status": "REVIEW_INFRASTRUCTURE_READY"}
        summary = progress(frame, config)
        render_views(frame, config)
        print(
            json.dumps(
                {
                    **result,
                    "reviewer_id": config["reviewer_id"],
                    "reviewed": summary["reviewed"],
                    "remaining": summary["remaining"],
                    "batches": summary["batches_created"],
                    "first_pass_status": summary["status"],
                    "full_extraction_authorized": False,
                }
            )
        )
        return 0
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        sqlite3.DatabaseError,
        workflow.sec.AcquisitionError,
    ) as error:
        reason = str(error) if isinstance(error, ValueError) else type(error).__name__
        print(
            json.dumps(
                {
                    "status": "REVIEW_ADMIN_STOPPED",
                    "reason": reason,
                    "no_decisions_repaired": True,
                    "full_extraction_authorized": False,
                }
            ),
            file=sys.stderr,
        )
        return 2
    finally:
        if locked:
            (ADMIN / "review-admin.lock").unlink()


if __name__ == "__main__":
    raise SystemExit(main())
