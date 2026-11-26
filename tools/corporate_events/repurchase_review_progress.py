"""Track source-bound human feedback without changing frozen predictions or QA gates."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from html import escape
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "protocols/corporate-event-alpha-v1"
PRIVATE = ROOT / "reports/corporate-event-alpha-v1/phase2-private"
OUTPUT = PRIVATE / "repurchase-review-progress-v1"
REGISTERED = {
    "tools/corporate_events/repurchase_review_progress.py",
    "tests/test_repurchase_review_progress.py",
    "protocols/corporate-event-alpha-v1/repurchase-review-progress-policy-v1.json",
}
spec = importlib.util.spec_from_file_location(
    "progress_triage", ROOT / "tools/corporate_events/repurchase_review_triage.py"
)
assert spec and spec.loader
triage: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = triage
spec.loader.exec_module(triage)
sec, ledger = triage.sec, triage.ledger


def read_fact(cases: dict[str, Any], locator: dict[str, Any]) -> Any:
    value = cases[locator["case_key"]]
    for key in locator["path"]:
        value = value[key]
    return value


def compare(
    targets: list[dict[str, Any]], decisions: list[dict[str, Any]], cases: dict[str, Any]
) -> list[dict[str, Any]]:
    ids = [d["question_id"] for d in decisions]
    if len(ids) != len(set(ids)) or set(ids) != {t["question_id"] for t in targets}:
        raise ValueError("FEEDBACK_TARGET_MEMBERSHIP_OR_DUPLICATE_INVALID")
    by_id = {d["question_id"]: d for d in decisions}
    result = []
    for target in targets:
        decision = by_id[target["question_id"]]
        if decision.get("independent_gold_label") is not False:
            raise ValueError("EXCERPT_FEEDBACK_CANNOT_DECLARE_INDEPENDENT_GOLD")
        case_keys = target["case_keys"]
        if not case_keys or any(cases[k]["accession"] != decision["accession"] for k in case_keys):
            raise ValueError("FEEDBACK_CASE_ACCESSION_MISMATCH")
        if any(r["case_key"] not in case_keys for r in target["fact_reads"].values()):
            raise ValueError("FEEDBACK_FACT_OUTSIDE_DECLARED_CASES")
        predicted = {key: read_fact(cases, loc) for key, loc in target["fact_reads"].items()}
        choice = decision["choice"]
        if choice not in target["choice_values"]:
            raise ValueError("FEEDBACK_CHOICE_NOT_OFFERED")
        observed = target["choice_values"][choice]
        if observed is not None and set(observed) != set(predicted):
            raise ValueError("FEEDBACK_FACT_SCHEMA_MISMATCH")
        source_text = "\n".join(s["text"] for s in decision["source_spans"])
        if any(needle not in source_text for needle in target["required_display_text"]):
            raise ValueError("FEEDBACK_CRITICAL_TEXT_NOT_SHOWN")
        status = (
            "UNRESOLVED"
            if observed is None
            else "AGREES_WITH_CONTEXT"
            if observed == predicted
            else "DISAGREES_WITH_CONTEXT"
        )
        result.append(
            {
                "question_id": target["question_id"],
                "issue_type": target["issue_type"],
                "accession": decision["accession"],
                "case_keys": case_keys,
                "choice": choice,
                "frozen_context_prediction": predicted,
                "fact_locators": target["fact_reads"],
                "human_interpretation": observed,
                "status": status,
                "confirmed_flag_codes": target["resolved_flag_codes"]
                if status == "AGREES_WITH_CONTEXT"
                else [],
                "independent_gold_label": False,
                "full_filing_review_complete": False,
            }
        )
    return result


def remaining_queue(
    filings: list[dict[str, Any]],
    case_scores: list[dict[str, Any]],
    passage_scores: list[dict[str, Any]],
    cases: dict[str, Any],
    comparisons: list[dict[str, Any]],
    threshold: int,
) -> list[dict[str, Any]]:
    resolved_cases: dict[str, set[str]] = defaultdict(set)
    resolved_records: dict[str, set[str]] = defaultdict(set)
    feedback: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in comparisons:
        feedback[observation["accession"]].append(observation)
        for key in observation["case_keys"]:
            resolved_cases[key].update(observation["confirmed_flag_codes"])
            for record in cases[key]["members"]:
                resolved_records[record].update(observation["confirmed_flag_codes"])
    open_scores: dict[str, list[int]] = defaultdict(list)
    open_codes: dict[str, set[str]] = defaultdict(set)
    for group, index_key, resolved in (
        (case_scores, "case_key", resolved_cases),
        (passage_scores, "record_id", resolved_records),
    ):
        for scored in group:
            flags = [f for f in scored["flags"] if f["code"] not in resolved[scored[index_key]]]
            open_scores[scored["accession"]].append(triage.points(flags)[0])
            open_codes[scored["accession"]].update(f["code"] for f in flags)
    result = []
    for original in filings:
        acc = original["accession"]
        unresolved_points = max(open_scores[acc], default=0)
        if unresolved_points > original["risk_points"]:
            raise ValueError("ORIGINAL_SCORE_RECONSTRUCTION_MISMATCH")
        observations = feedback[acc]
        adjudication = any(o["status"] != "AGREES_WITH_CONTEXT" for o in observations)
        result.append(
            {
                "accession": acc,
                "original_risk_points": original["risk_points"],
                "original_priority_flag": original["priority_review_flag"],
                "remaining_review_priority_points": unresolved_points,
                "remaining_flag_codes": sorted(open_codes[acc]),
                "remaining_priority_flag": unresolved_points >= threshold or adjudication,
                "human_confirmed_questions": sum(
                    o["status"] == "AGREES_WITH_CONTEXT" for o in observations
                ),
                "adjudication_required": adjudication,
                "correctness_probability": None,
                "fully_reviewed": False,
                "automatic_acceptance": False,
                "research_qualifying": False,
            }
        )
    return result


def baseline_conflicts(
    observations: list[dict[str, Any]], cases: dict[str, Any], rows: dict[str, Any]
) -> list[dict[str, Any]]:
    result = []
    for observation in observations:
        if observation["status"] != "AGREES_WITH_CONTEXT":
            continue
        locator = observation["fact_locators"].get("action_kind")
        if locator is None or locator["path"] != ["action_kind"]:
            continue
        key = locator["case_key"]
        expected = observation["human_interpretation"]["action_kind"]
        disagree = [
            member
            for member in cases[key]["members"]
            if rows[member]["proposed_state"] not in {"UNRESOLVED", expected}
        ]
        if disagree:
            result.append(
                {
                    "question_id": observation["question_id"],
                    "case_key": key,
                    "original_parser_disagreeing_record_ids": disagree,
                }
            )
    return result


def build() -> dict[str, Any]:
    _, baseline, _ = triage.preflight()
    registry = json.loads((PROTOCOL / "repurchase-review-progress-extension-v1.json").read_bytes())
    if set(registry["files"]) != REGISTERED:
        raise ValueError("PROGRESS_REGISTRY_SCOPE_INVALID")
    for name, digest in registry["files"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("PROGRESS_REGISTERED_CODE_CHANGED")
    policy = json.loads((PROTOCOL / "repurchase-review-progress-policy-v1.json").read_bytes())
    if policy["automatic_acceptance"] or policy["independent_gold_from_owner_excerpt_checks"]:
        raise ValueError("PROGRESS_CANNOT_WAIVE_QA")
    sec.EXTENSION_FILES = (
        *sec.EXTENSION_FILES,
        *REGISTERED,
        *triage.REGISTERED,
        *triage.context.REGISTERED,
        *ledger.REGISTERED,
        *baseline["parser_files"],
    )
    sec.install_firewall()
    sys.addaudithook(
        lambda event, args: (
            (_ for _ in ()).throw(RuntimeError("PROGRESS_OFFLINE_ONLY"))
            if event.startswith(("socket.", "http.client.", "subprocess."))
            else None
        )
    )
    inputs = {}
    for name, digest in policy["private_inputs"].items():
        path = (PRIVATE / name).resolve()
        if not path.is_relative_to(PRIVATE.resolve()) or sec.file_sha(path) != digest:
            raise ValueError("PROGRESS_PINNED_INPUT_CHANGED")
        inputs[name] = path

    def load(name: str) -> Any:
        return json.loads(inputs[name].read_bytes())

    receipt = load("triage-human-excerpt-check-v1-decisions.json")
    display = load("triage-human-excerpt-check-v1-display.json")
    if (
        receipt["display_frame_sha256"]
        != sec.file_sha(inputs["triage-human-excerpt-check-v1-display.json"])
        or receipt["question_frame_sha256"]
        != sec.file_sha(inputs["triage-human-excerpt-check-v1.json"])
        or receipt["reviewer_id"] != display["reviewer_id"]
        or receipt["independent_gold_labels_added"] != 0
    ):
        raise ValueError("PROGRESS_HUMAN_RECEIPT_BINDING_INVALID")
    frozen_manifest = triage.OUTPUT / "output-manifest.json"
    if sec.file_sha(frozen_manifest) != receipt["triage_manifest_sha256"]:
        raise ValueError("PROGRESS_FROZEN_TRIAGE_CHANGED")
    for name, digest in json.loads(frozen_manifest.read_bytes())["files"].items():
        path = (triage.OUTPUT / name).resolve()
        if not path.is_relative_to(triage.OUTPUT.resolve()) or sec.file_sha(path) != digest:
            raise ValueError("PROGRESS_FROZEN_TRIAGE_REPLAY_FAILED")
    coverage = json.loads((ledger.OUTPUT / "source-coverage.json").read_bytes())["components"]
    components = {c["component_id"]: c for c in coverage}
    displayed = {q["id"]: q for q in display["questions"]}
    for decision in receipt["decisions"]:
        question = displayed[decision["question_id"]]
        if (
            decision["source_spans"] != question["display_spans"]
            or decision["packet_id"] != question["packet_id"]
            or decision["accession"] != question["accession"]
            or decision["component_id"] != question["component_id"]
            or decision["full_source_review_attested"] is not False
        ):
            raise ValueError("PROGRESS_DISPLAY_SCOPE_MISMATCH")
        c = components[decision["component_id"]]
        if (
            c["accession"] != decision["accession"]
            or c["container_sha256"] != decision["source_container_sha256"]
        ):
            raise ValueError("PROGRESS_COMPONENT_ACCESSION_MISMATCH")
        text_path = (ledger.OUTPUT / c["derived_text_file"]).resolve()
        if not text_path.is_relative_to(ledger.OUTPUT.resolve()):
            raise ValueError("PROGRESS_DERIVED_PATH_OUTSIDE_LEDGER")
        text_raw = text_path.read_bytes()
        if sec.sha(text_raw) != c["derived_text_sha256"]:
            raise ValueError("PROGRESS_DERIVED_TEXT_CHANGED")
        text = text_raw.decode("utf-8")
        raw = sec.Cache(ledger.acq.OUTPUT).read(c["container_sha256"])
        if (
            sec.sha(raw[c["container_byte_start"] : c["container_byte_end"]])
            != c["raw_document_sha256"]
        ):
            raise ValueError("PROGRESS_RAW_COMPONENT_CHANGED")
        for span in decision["source_spans"]:
            if (
                text[span["text_start"] : span["text_end"]] != span["text"]
                or sec.sha(span["text"].encode()) != span["sha256"]
            ):
                raise ValueError("PROGRESS_EXCERPT_REPLAY_FAILED")

    def read_rows(path: Path) -> list[dict[str, Any]]:
        return [json.loads(line) for line in path.read_bytes().splitlines()]

    cases = {c["case_key"]: c for c in read_rows(triage.context.OUTPUT / "actions.jsonl")}
    targets = load("review-progress-targets-v1.json")["targets"]
    observations = compare(targets, receipt["decisions"], cases)
    filings = read_rows(triage.OUTPUT / "filing-risk-index.jsonl")
    queue = remaining_queue(
        filings,
        read_rows(triage.OUTPUT / "case-risk-index.jsonl"),
        read_rows(triage.OUTPUT / "passage-risk-index.jsonl"),
        cases,
        observations,
        policy["priority_threshold"],
    )
    original_queue = json.loads((triage.OUTPUT / "operator-review-queue.json").read_bytes())
    selected = {u["accession"] for u in original_queue["selected"]}
    legacy_rows = {r["record_id"]: r for r in read_rows(ledger.OUTPUT / "ledger.jsonl")}
    legacy_conflicts = baseline_conflicts(observations, cases, legacy_rows)
    by_issue: dict[str, Counter[str]] = defaultdict(Counter)
    for item in observations:
        by_issue[item["issue_type"]][item["status"]] += 1
    summary = {
        "version": "repurchase-review-progress-v1",
        "prediction_stage_compared": "FROZEN_SOURCE_CONTEXT_RESOLUTION",
        "human_questions_answered": len(observations),
        "agrees_with_context": sum(o["status"] == "AGREES_WITH_CONTEXT" for o in observations),
        "by_issue_type": {k: dict(v) for k, v in by_issue.items()},
        "original_parser_conflict_questions": len({c["question_id"] for c in legacy_conflicts}),
        "filings_tracked": len(queue),
        "original_priority_filings": sum(q["original_priority_flag"] for q in queue),
        "remaining_priority_filings": sum(q["remaining_priority_flag"] for q in queue),
        "first_wave_remaining_priority_filings": sum(
            q["remaining_priority_flag"] and q["accession"] in selected for q in queue
        ),
        "first_wave_membership_changes": 0,
        "fully_reviewed_filings": 0,
        "independent_gold_labels": 0,
        "calibrated_correctness_probability": None,
        "population_accuracy_estimate": None,
        "automatic_acceptance": False,
        "source_scope_supported_current_candidates": sum(
            c["resolution_status"] == "CURRENT_RELEASE_SOURCE_SCOPE_SUPPORTED"
            for c in cases.values()
        ),
        "research_qualifying_events": 0,
        "original_718_sample_changes": 0,
        "new_network_requests": 0,
        "full_extraction_authorized": False,
        "final_qa_authorized": False,
        "phase3_authorized": False,
        "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
    }

    def write(name: str, data: Any) -> None:
        ledger.immutable(OUTPUT / name, sec.json_bytes(data))

    write("comparisons.json", {"observations": observations, "legacy_conflicts": legacy_conflicts})
    write("remaining-review-queue.json", {"filings": queue})
    readiness = []
    for key, case in cases.items():
        readiness.append(
            {
                "case_key": key,
                "company": case["company"],
                "source_scope_status": case["resolution_status"],
                "action_kind": case["action_kind"],
                "capacities": case["capacities"],
                "source_stated_announcement_date": case["facts"]["stated_announcement_date"],
                "human_confirmed_question_ids": [
                    o["question_id"]
                    for o in observations
                    if key in o["case_keys"] and o["status"] == "AGREES_WITH_CONTEXT"
                ],
                "remaining_requirements": case["unresolved_requirements"],
                "research_qualifying": False,
            }
        )
    write("candidate-readiness.json", {"actions": readiness})
    write("summary.json", summary)
    rows = "".join(
        f"<tr><td>{escape(q['accession'])}</td><td>{q['original_risk_points']}</td>"
        f"<td>{q['remaining_review_priority_points']}</td>"
        f"<td>{q['human_confirmed_questions']}</td>"
        f"<td>{escape(', '.join(q['remaining_flag_codes']))}</td></tr>"
        for q in queue
        if q["original_priority_flag"]
    )
    actions_html = "".join(
        f"<tr><td>{escape(c['company'])}</td><td>{escape(c['action_kind'])}</td>"
        f"<td>{escape(str(c['source_stated_announcement_date']))}</td>"
        f"<td>{escape(', '.join(c['remaining_requirements']))}</td></tr>"
        for c in readiness
    )
    page = (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'\">"
        "<title>Repurchase review progress</title><style>"
        "body{font:16px/1.5 system-ui;max-width:1200px;margin:auto;padding:24px}"
        "td,th{padding:8px;border:1px solid #ddd;text-align:left}table{border-collapse:collapse}"
        "</style><h1>Repurchase review progress</h1>"
        f"<p>{summary['agrees_with_context']} of {len(observations)} displayed fact checks "
        "agree with frozen source-context interpretations. Targeted development feedback; "
        "no population accuracy or calibrated probability is established.</p>"
        f"<p>{summary['remaining_priority_filings']} of "
        f"{summary['original_priority_filings']} original priority filings retain open "
        "interpretation flags. Zero points do not certify a filing.</p>"
        f"<p>Original parser conflicts retained: {summary['original_parser_conflict_questions']} "
        "reviewed question(s). Full-filing QA complete: 0. Research events accepted: 0.</p>"
        "<table><tr><th>Filing</th><th>Original points</th><th>Remaining review points</th>"
        "<th>Confirmed questions</th><th>Remaining flags</th></tr>" + rows + "</table>"
        "<h2>Action readiness</h2><p>Source-stated dates are not certified public times. "
        "Original requirements remain open; excerpt feedback does not certify entire actions.</p>"
        "<table><tr><th>Company</th><th>Action</th><th>Stated announcement date</th>"
        "<th>Remaining requirements</th></tr>" + actions_html + "</table></html>"
    )
    ledger.immutable(OUTPUT / "progress.html", page.encode())
    write(
        "output-manifest.json",
        {
            "files": {
                p.name: sec.file_sha(p)
                for p in OUTPUT.iterdir()
                if p.is_file() and p.name != "output-manifest.json"
            },
            "code_registry": registry,
            "source_triage_manifest_sha256": sec.file_sha(frozen_manifest),
            "decision_receipt_sha256": sec.file_sha(
                inputs["triage-human-excerpt-check-v1-decisions.json"]
            ),
        },
    )
    return summary


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("command", choices=["build", "validate"])
    cli.parse_args()
    try:
        print(json.dumps(build()), flush=True)
    except (ValueError, KeyError, OSError, RuntimeError, sec.AcquisitionError) as error:
        print(json.dumps({"status": "PROGRESS_STOPPED", "reason": str(error)}), flush=True)
        raise SystemExit(2) from None
