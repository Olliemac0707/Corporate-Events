"""Run a separately registered development parser on the exact frozen local sources."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "protocols/corporate-event-alpha-v1"
PRIVATE = ROOT / "reports/corporate-event-alpha-v1/phase2-private"
OUTPUT = PRIVATE / "parser-development-v2/runs"
POLICY = PROTOCOL / "parser-development-v2-policy.json"
EXTENSION = PROTOCOL / "parser-development-v2-extension.json"
REGISTERED = (
    "tools/corporate_events/event_parsers_v2.py",
    "tools/corporate_events/parser_development_v2.py",
    "tests/test_event_parsers_v2.py",
    "tests/test_parser_development_v2.py",
    "protocols/corporate-event-alpha-v1/parser-development-v2-policy.json",
)


def load(name: str, relative: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


review = load("cea_v2_baseline_review", "tools/corporate_events/confidence_review.py")
parser = load("cea_v2_candidate_parser", "tools/corporate_events/event_parsers_v2.py")


def offline_audit(event: str, args: tuple[Any, ...]) -> None:
    del args
    if event.startswith(("socket.", "http.client.")):
        raise RuntimeError("V2_OFFLINE_NETWORK_FORBIDDEN")


def check_policy(policy: dict[str, Any]) -> None:
    if (
        policy.get("version") != "parser-development-v2"
        or policy.get("sample_sha256") != review.admin.EXPECTED_SHA
    ):
        raise ValueError("V2_POLICY_IDENTITY_INVALID")
    if policy.get("sample_records") != 718 or policy.get("sample_cells") != 297:
        raise ValueError("V2_FIXED_FRAME_REQUIRED")
    if (
        policy.get("authorization")
        != "USER_GO_AHEAD_NEW_PARSER_DEVELOPMENT_AFTER_ALL718_INITIAL_DISPOSITIONS"
    ):
        raise ValueError("V2_AUTHORIZATION_SCOPE_INVALID")
    for key in (
        "network_allowed",
        "market_data_allowed",
        "new_filings_allowed",
        "final_qa_authorized",
        "full_extraction_authorized",
        "phase3_authorized",
        "baseline_overwrite_allowed",
        "sample_redraw_allowed",
    ):
        if policy.get(key) is not False:
            raise ValueError("V2_CLOSED_GATE_REQUIRED:" + key)


def validate_frame(
    frame: dict[str, Any],
    qa: dict[str, Any],
    confidence_policy: dict[str, Any],
    proof: dict[str, Any],
) -> None:
    expected = review.admin.batches(qa)
    selected = {row["source_id"]: row for row in qa["selected"]}
    for row in expected:
        row["source_parse_status"] = selected[row["source_id"]]["parse_status"]
    if (
        frame["records"] != expected
        or frame["sample_sha256"] != review.admin.EXPECTED_SHA
        or frame["policy"] != confidence_policy
        or frame["policy_sha256"] != proof["policy_sha256"]
    ):
        raise ValueError("V2_FRAME_DOES_NOT_MATCH_FROZEN_SOURCE_SELECTION")


def verify_evidence(source: Any, parsed: dict[str, Any]) -> int:
    text = parsed["text"]
    if parser.base.digest(text.encode()) != parsed["derived_text_sha256"]:
        raise ValueError("V2_DERIVED_TEXT_HASH_MISMATCH")
    count = 0
    evidence_list = []
    for row in parsed["observations"]:
        if row["certified_qualifying"] or row["review_status"] != "PENDING":
            raise ValueError("V2_AUTOMATIC_CERTIFICATION_FORBIDDEN")
        evidence_list.extend([row["evidence"], *row["additional_evidence"]])
    evidence_list.extend(
        item["evidence"] for item in parsed["retained_contexts"] if "evidence" in item
    )
    for proof in evidence_list:
        start, end = proof["text_start"], proof["text_end"]
        if not (0 <= start < end <= len(text)):
            raise ValueError("V2_EVIDENCE_RANGE_INVALID")
        if (
            proof["source_document_sha256"] != source.sha256
            or proof["derived_text_sha256"] != parsed["derived_text_sha256"]
            or proof["evidence_sha256"] != parser.base.digest(text[start:end].encode())
            or proof["extractor_version"] != parser.VERSION
        ):
            raise ValueError("V2_EVIDENCE_BINDING_INVALID")
        count += 1
    return count


def unchanged_snapshot() -> dict[str, str]:
    names = [
        review.PILOT / "frozen-development-qa-selection.json",
        review.RUN / "development-qa-selection.json",
        review.RUN / "run-manifest.json",
        review.RUN / "fact-review-history.jsonl",
        review.admin.ADMIN / "completion-ledger.jsonl",
        review.OUTPUT / "ai-assessments.jsonl",
        review.OUTPUT / "frame.json",
        review.OUTPUT / "all-records-initial-pass-validation.json",
    ]
    return {str(path.relative_to(ROOT)): review.digest(path.read_bytes()) for path in names}


def run(run_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"offline-development-v2-[0-9]{3}", run_id):
        raise ValueError("V2_RUN_ID_INVALID")
    target = OUTPUT / run_id
    if target.exists():
        raise ValueError("V2_IMMUTABLE_RUN_ALREADY_EXISTS")
    qa, confidence_policy, proof = review.preflight()
    policy = review.read(POLICY)
    check_policy(policy)
    extension = review.read(EXTENSION)
    if set(extension["files"]) != set(REGISTERED):
        raise ValueError("V2_EXTENSION_SCOPE_INVALID")
    for name, expected in extension["files"].items():
        if review.digest((ROOT / name).read_bytes()) != expected:
            raise ValueError("V2_CODE_HASH_MISMATCH:" + name)
    baseline_validation = review.read(review.OUTPUT / "all-records-initial-pass-validation.json")
    if (
        baseline_validation["active_ai_assessments"] != 718
        or baseline_validation["ai_pending"] != 0
        or baseline_validation["status"] != "PASS_WITH_REVIEW_LIMITATIONS"
    ):
        raise ValueError("V2_BASELINE_INITIAL_PASS_NOT_COMPLETE")
    frame = review.read(review.OUTPUT / "frame.json")
    validate_frame(frame, qa, confidence_policy, proof)
    _, latest = review.history(frame)
    if len(latest) != 718:
        raise ValueError("V2_BASELINE_REVIEW_JOURNAL_INCOMPLETE")
    before = unchanged_snapshot()
    review.workflow.sec.EXTENSION_FILES = (*review.workflow.sec.EXTENSION_FILES, *REGISTERED)
    review.install_guard()
    sys.addaudithook(offline_audit)
    target.mkdir(parents=True)
    (target / "sources").mkdir()
    (target / "derived-text").mkdir()
    files: dict[str, str] = {}

    def write(relative: str, value: Any, *, raw: bool = False) -> None:
        data = value if raw else review.canonical(value)
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError("V2_OUTPUT_CONFLICT")
        else:
            review.workflow.sec.write_new(path, data)
        files[relative] = review.digest(data)

    write(
        "input-manifest.json",
        {
            "policy": policy,
            "extension": extension,
            "baseline_proof": proof,
            "baseline_snapshot_hashes": before,
            "frozen_sample_sha256": review.admin.EXPECTED_SHA,
            "comparison_kind": "DEVELOPMENT_REGRESSION_NOT_INDEPENDENT_VALIDATION",
        },
    )
    for name, expected in extension["files"].items():
        source_bytes = (ROOT / name).read_bytes()
        if review.digest(source_bytes) != expected:
            raise ValueError("V2_CODE_CHANGED_DURING_RUN")
        write("code/" + name, source_bytes, raw=True)
    plan = review.read(review.PILOT / "plan.json")
    if plan["max_submissions"] != 108 or plan["max_received_body_bytes"] != 536870912:
        raise ValueError("V2_ORIGINAL_PILOT_BOUNDS_CHANGED")
    entries = {row["accession"]: row for row in plan["entries"]}
    aliases = {row["form"]: row["canonical_form"] for row in plan["entries"]}
    cache = review.workflow.sec.Cache(review.PILOT)
    header_cache: dict[str, Any] = {}
    source_results = {}
    observations = []
    evidence_count = 0
    unsupported = 0
    context_counts: Counter[str] = Counter()
    for case in frame["records"]:
        source_id = case["source_id"]
        if source_id in source_results:
            continue
        packet = review.admin.packet_for(case)
        raw = (review.RUN / packet["original_component_file"]).read_bytes()
        if review.digest(raw) != case["source_document_sha256"]:
            raise ValueError("V2_ORIGINAL_COMPONENT_HASH_MISMATCH")
        source = parser.base.Document(
            packet["accession"],
            packet["filename"],
            packet["raw_doc_form"],
            packet["source_kind"],
            "",
            packet["container_byte_start"],
            packet["container_byte_end"],
            raw,
        )
        if source.source_id != source_id:
            raise ValueError("V2_ORIGINAL_SOURCE_ID_MISMATCH")
        canonical = aliases.get(packet["form"])
        entry = entries.get(packet["accession"])
        if not canonical or not entry:
            raise ValueError("V2_SOURCE_NOT_IN_ORIGINAL_PILOT")
        if packet["container_sha256"] not in header_cache:
            if len(header_cache) >= 108:
                raise ValueError("V2_ORIGINAL_PILOT_CONTAINER_CAP")
            container = cache.read(packet["container_sha256"])
            if container[source.start : source.end] != raw:
                raise ValueError("V2_CONTAINER_COMPONENT_BYTES_MISMATCH")
            _, header = parser.base.resolve_submission(container, packet["accession"])
            header_cache[packet["container_sha256"]] = header
        header = header_cache[packet["container_sha256"]]
        parsed: dict[str, Any]
        if packet["parse_status"] == "UNSUPPORTED_COMPONENT_RETAINED":
            parsed = {
                "status": "FROZEN_UNSUPPORTED_COMPONENT_RETAINED",
                "observations": [],
                "retained_contexts": [],
                "coverage_status": "UNSUPPORTED_SEMANTIC_REVIEW_UNRESOLVED",
            }
            unsupported += 1
        elif canonical in {"4", "4/A"} and source.kind == "OWNERSHIP_XML":
            parsed = parser.ownership(source)
            for row in parsed["observations"]:
                row["additional_evidence"] = []
                row["implementation_version"] = parser.base.VERSION
        else:
            parsed = parser.narrative(source, canonical, entry["cik_raw"], roles=header)
            evidence_count += verify_evidence(source, parsed)
            text = parsed.pop("text")
            write("derived-text/" + parsed["derived_text_sha256"] + ".txt", text.encode(), raw=True)
            context_counts.update(parsed["context_reason_counts"])
        for index, row in enumerate(parsed["observations"]):
            row["accession"] = packet["accession"]
            row["source_id"] = source_id
            row["development_observation_id"] = parser.base.stable(
                parser.VERSION, source_id, row["family"], row["economic_action_anchor"], index
            )
            observations.append(row)
        source_results[source_id] = parsed
        write(
            "sources/" + source_id + ".json",
            {
                "source_id": source_id,
                "source_document_sha256": source.sha256,
                "original_component_file": packet["original_component_file"],
                "canonical_form": canonical,
                "result": parsed,
            },
        )
        if len(source_results) % 75 == 0:
            print(json.dumps({"sources_processed": len(source_results)}), flush=True)
    comparisons = []
    for case in frame["records"]:
        packet = review.admin.packet_for(case)
        old = [p for p in packet["candidate_proposals"] if p["family"] == case["family"]]
        new = [
            p
            for p in source_results[case["source_id"]]["observations"]
            if p["family"] == case["family"] and p["parser_candidate"]
        ]
        comparisons.append(
            {
                "sequence": case["sequence"],
                "sample_record_id": case["sample_record_id"],
                "source_id": case["source_id"],
                "family": case["family"],
                "role": case["role"],
                "baseline_proposal_count": len(old),
                "v2_proposal_count": len(new),
                "baseline_states": dict(Counter(p["proposed_state"] for p in old)),
                "v2_states": dict(Counter(p["proposed_state"] for p in new)),
                "v2_review_status": "PENDING_INDEPENDENT_REVIEW",
                "empty_v2_means": "NO_V2_PROPOSAL_NOT_A_NO_EVENT_GOLD_LABEL",
                "baseline_ai_disposition": latest[case["sample_record_id"]]["assessment"][
                    "classification_state"
                ],
                "baseline_ai_is_independent_gold": False,
            }
        )
    write("record-comparison.json", {"sample_records": 718, "records": comparisons})
    write("documentary-link-suggestions.json", parser.documentary_links(observations))
    for name, expected in extension["files"].items():
        if review.digest((ROOT / name).read_bytes()) != expected:
            raise ValueError("V2_CODE_CHANGED_DURING_RUN")
    after = unchanged_snapshot()
    if after != before:
        raise ValueError("V2_FROZEN_BASELINE_CHANGED")
    summary = {
        "status": "OFFLINE_DEVELOPMENT_RUN_COMPLETE_INDEPENDENT_QA_PENDING",
        "parser_version": parser.VERSION,
        "run_id": run_id,
        "sample_sha256": review.admin.EXPECTED_SHA,
        "sample_records": 718,
        "sample_cells": 297,
        "source_results": len(source_results),
        "cached_containers": len(header_cache),
        "unsupported_sources_retained": unsupported,
        "evidence_locators_checked": evidence_count,
        "baseline_unchanged": True,
        "candidate_observations": sum(bool(p["parser_candidate"]) for p in observations),
        "candidate_counts_by_family": dict(
            Counter(p["family"] for p in observations if p["parser_candidate"])
        ),
        "retained_context_counts": dict(context_counts),
        "all_records_compared": len(comparisons),
        "accuracy": "NOT_MEASURED",
        "precision": "NOT_MEASURED",
        "recall": "NOT_MEASURED",
        "confidence_calibration": "NOT_MEASURED",
        "independent_validation_complete": False,
        "network_requests": 0,
        "new_filings": 0,
        "sample_redraws": 0,
        "historical_schema_authority_changed": False,
        "final_qa_authorized": False,
        "full_extraction_authorized": False,
        "phase3_authorized": False,
        "firewall_counters": dict.fromkeys(review.workflow.sec.FIREWALL_COUNTERS, 0),
        "limitations": [
            "Regression development on reviewed sources; not an independent or holdout assessment.",
            "Fewer proposals are not evidence of higher precision or preserved recall.",
            (
                "Unsupported formats, partial source coverage, public ti"
                "ming, dividend baselines and first disclosure remain un"
                "resolved."
            ),
            "Link suggestions require review; no automatic final action deduplication.",
            "Original confidence scores and human judgments are not transferred to v2 proposals.",
        ],
    }
    write("summary.json", summary)
    report = (
        "# Parser development v2: offline regression run\n\n"
        f"Compared all718 frozen records across {len(source_results)} sources. "
        f"Validated {evidence_count} narrative evidence locators. "
        "The frozen parser, sample, original assessments and three human "
        "decisions are unchanged.\n\n"
        "This is development output, not independent validation. No score or clearance transfers "
        "from the old review. Proposal-count changes do not measure precision or recall.\n\n"
        "All outcome-firewall counters, network requests and new acquisitions are zero. "
        "Final QA, full extraction and Phase3 remain closed.\n"
    )
    write("report.md", report.encode(), raw=True)
    manifest = {
        "kind": "IMMUTABLE_OFFLINE_DEVELOPMENT_V2_RUN",
        "run_id": run_id,
        "policy_sha256": review.digest(POLICY.read_bytes()),
        "files": files,
        "baseline_before": before,
        "baseline_after": after,
    }
    review.workflow.sec.write_new(target / "run-manifest.json", review.canonical(manifest))
    print(json.dumps(summary), flush=True)
    return summary


def main() -> None:
    args = argparse.ArgumentParser()
    args.add_argument("--run-id", required=True)
    run_id = args.parse_args().run_id
    try:
        run(run_id)
    except Exception as error:
        if re.fullmatch(r"offline-development-v2-[0-9]{3}", run_id):
            target = OUTPUT / run_id
            if target.exists() and not (target / "run-manifest.json").exists():
                failure = {
                    "status": "INCOMPLETE_DEVELOPMENT_RUN",
                    "error_type": type(error).__name__,
                    "reason": str(error),
                    "baseline_overwrite_allowed": False,
                }
                path = target / "failure.json"
                if not path.exists():
                    review.workflow.sec.write_new(path, review.canonical(failure))
        raise


if __name__ == "__main__":
    main()
