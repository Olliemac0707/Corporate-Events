"""Synthetic review-administration tests. No real review decisions or source inspection."""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "tools/corporate_events/review_admin.py"
spec = importlib.util.spec_from_file_location("review_admin_test", ENTRY)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
REVIEWER = "SYNTHETIC_TEST_HUMAN"
SOURCE = "1" * 64
PROOF = "2" * 64
EVENT = "3" * 64


def row(index: int = 0, role: str = "INDEPENDENT_SOURCE_CONTROL") -> dict[str, Any]:
    return {
        "source_id": f"{index + 1:064x}",
        "source_document_sha256": "4" * 64,
        "source_evidence_id": PROOF,
        "family": "SHARE_REPURCHASE",
        "era": "2023-2024",
        "form": "8-K",
        "source_kind": "BODY",
        "role": role,
    }


def bundle() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    case = m.batches({"selected": [row()]})[0]
    packet = {
        "candidate_proposals": [],
        "source_evidence_id": PROOF,
        "container_sha256": "5" * 64,
        "container_locator": "synthetic-only",
    }
    evidence = {PROOF: (case["source_id"], "5" * 64, "synthetic-only", "container-bytes:0:10")}
    record = {
        "sample_record_id": case["sample_record_id"],
        "human_attested": True,
        "first_pass_status": "REVIEWED_UNRESOLVED",
        "concerns": [],
        "review": {
            "source_id": case["source_id"],
            "event_id": None,
            "reviewer_id": REVIEWER,
            "decision": "AMBIGUOUS",
            "reason": "Synthetic fixture, never a real decision.",
            "evidence_ids": [PROOF],
            "supersedes_review_id": None,
            "scope": "DEVELOPMENT_FACT_REVIEW_ONLY",
            "family": case["family"],
            "role": case["role"],
            "correct_state": "UNRESOLVED",
            "event_presence": None,
        },
    }
    return case, packet, evidence, record


def test_batches_stably_partition_original_frame_and_keep_all_records() -> None:
    selected = [
        row(i, "PARSER_CANDIDATE" if i % 3 == 0 else "INDEPENDENT_SOURCE_CONTROL")
        for i in range(718)
    ]
    result = m.batches({"selected": selected})
    assert len(result) == 718 and len({x["sample_record_id"] for x in result}) == 718
    assert len({x["batch_id"] for x in result}) == 29
    assert sum(x["batch_id"] == "batch-029" for x in result) == 18
    original_positions = [
        i for i, x in enumerate(selected) if x["role"] == "INDEPENDENT_SOURCE_CONTROL"
    ]
    original_positions += [i for i, x in enumerate(selected) if x["role"] == "PARSER_CANDIDATE"]
    assert [x["frozen_selection_index"] for x in result] == original_positions
    assert m.batches({"selected": selected}) == result


@pytest.mark.parametrize("name", ["AI", "Codex", "system", "automation", "parser", "model", ""])
def test_nonhuman_or_unset_reviewer_is_rejected(name: str) -> None:
    assert not m.valid_reviewer(name)


def test_human_pseudonym_is_accepted_without_invention() -> None:
    assert m.valid_reviewer("Review_TEST01")


@pytest.mark.parametrize(
    "change",
    [
        "unknown_field",
        "return_field",
        "wrong_source",
        "wrong_family",
        "wrong_role",
        "wrong_reviewer",
        "wrong_state",
        "blank_reason",
        "empty_evidence",
        "unknown_evidence",
        "unregistered_evidence",
        "wrong_evidence_source",
        "event_in_control",
        "no_attestation",
        "bad_status",
        "unknown_concern",
        "concern_without_description",
        "wrong_presence",
        "wrong_decision",
        "wrong_sample",
    ],
)
def test_invalid_human_input_rejected_without_repair(change: str) -> None:
    case, packet, evidence, record = bundle()
    if change == "unknown_field":
        record["unexpected"] = True
    elif change == "return_field":
        record["review"]["CAR"] = 0
    elif change == "wrong_source":
        record["review"]["source_id"] = "9" * 64
    elif change == "wrong_family":
        record["review"]["family"] = "ACTIVIST_13D"
    elif change == "wrong_role":
        record["review"]["role"] = "PARSER_CANDIDATE"
    elif change == "wrong_reviewer":
        record["review"]["reviewer_id"] = "different"
    elif change == "wrong_state":
        record["review"]["correct_state"] = "ACTIVIST_INTENT_SUPPORTED"
    elif change == "blank_reason":
        record["review"]["reason"] = "  "
    elif change == "empty_evidence":
        record["review"]["evidence_ids"] = []
    elif change == "unknown_evidence":
        record["review"]["evidence_ids"] = ["a" * 64]
    elif change == "unregistered_evidence":
        evidence = {}
    elif change == "wrong_evidence_source":
        evidence[PROOF] = ("wrong", "5" * 64, "synthetic-only", "locator")
    elif change == "event_in_control":
        record["review"]["event_id"] = EVENT
    elif change == "no_attestation":
        record["human_attested"] = False
    elif change == "bad_status":
        record["first_pass_status"] = "SKIPPED"
    elif change == "unknown_concern":
        record["concerns"] = [
            {
                "category": "MARKET_RESULT",
                "description": "x",
                "severity_candidate": None,
                "reviewer_note": "",
            }
        ]
    elif change == "concern_without_description":
        record["concerns"] = [
            {
                "category": "EVIDENCE_LOCATOR",
                "description": "",
                "severity_candidate": None,
                "reviewer_note": "",
            }
        ]
    elif change == "wrong_presence":
        record["review"]["event_presence"] = 1
    elif change == "wrong_decision":
        record["review"]["decision"] = "AUTO_ACCEPT"
    else:
        record["sample_record_id"] = "unknown"
    original = copy.deepcopy(record)
    with pytest.raises(ValueError):
        m.validate_record(record, case, REVIEWER, packet, evidence, m.labels(), None)
    assert record == original


def test_positive_finding_in_control_is_valid_not_assumed_negative() -> None:
    case, packet, evidence, record = bundle()
    record["review"].update(
        decision="ACCEPTED", correct_state="NEW_AUTHORIZATION", event_presence=True
    )
    record["first_pass_status"] = "REVIEWED"
    m.validate_record(record, case, REVIEWER, packet, evidence, m.labels(), None)


def test_supersession_references_latest_and_exact_replay_is_idempotent() -> None:
    case, packet, evidence, record = bundle()
    previous = {
        **record["review"],
        "review_id": "6" * 64,
        "input_sha256": m.digest(m.canonical(record["review"])),
    }
    m.validate_record(record, case, REVIEWER, packet, evidence, m.labels(), previous)
    record["review"]["reason"] = "Synthetic corrected note"
    with pytest.raises(ValueError, match="LATEST"):
        m.validate_record(record, case, REVIEWER, packet, evidence, m.labels(), previous)
    record["review"]["supersedes_review_id"] = previous["review_id"]
    m.validate_record(record, case, REVIEWER, packet, evidence, m.labels(), previous)


def test_candidate_submission_is_blocked_until_controls_already_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = m.batches({"selected": [row()]})[0]
    candidate = m.batches({"selected": [row(1, "PARSER_CANDIDATE")]})[0]
    frame = {"records": [control, candidate]}
    config = {"reviewer_id": REVIEWER}
    record = {"sample_record_id": candidate["sample_record_id"]}
    submission = {
        "version": "development-human-submission-v1",
        "batch_id": "batch-001",
        "reviewer_id": REVIEWER,
        "records": [record],
    }
    monkeypatch.setattr(
        m, "registered_evidence", lambda: pytest.fail("Barrier must precede packet access")
    )
    with pytest.raises(ValueError, match="BARRIER"):
        m.validate_submission(submission, frame, config, {}, {})


def test_completion_and_issue_journals_are_empty_at_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(m, "ADMIN", tmp_path / "admin")
    qa = {"selected": [row()]}
    first = m.prepare(REVIEWER, qa)
    before = (m.ADMIN / "batch-manifest.json").read_bytes()
    assert m.prepare(REVIEWER, qa) == first
    assert (m.ADMIN / "batch-manifest.json").read_bytes() == before
    assert (m.ADMIN / "completion-ledger.jsonl").read_bytes() == b""
    assert (m.ADMIN / "development-review-issues.jsonl").read_bytes() == b""
    with pytest.raises(ValueError):
        m.prepare("DIFFERENT_HUMAN", qa)


def synthetic_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], dict[str, Any]]:
    pilot = tmp_path / "synthetic-pilot"
    run = pilot / "runs" / m.RUN_ID
    run.mkdir(parents=True)
    monkeypatch.setattr(m, "PILOT", pilot)
    monkeypatch.setattr(m, "RUN", run)
    monkeypatch.setattr(m, "ADMIN", pilot / "human-review-v1")
    monkeypatch.setattr(m.workflow, "PILOT", pilot)
    case, packet, evidence, record = bundle()
    qa = {"selected": [row()]}
    m.prepare(REVIEWER, qa)
    packet.update(
        source_id=case["source_id"], source_document_sha256=case["source_document_sha256"]
    )
    m.write_new(run / "packets" / (case["source_id"] + ".json"), packet)
    m.write_new(pilot / "frozen-development-qa-selection.json", qa)
    m.write_new(
        run / "run-manifest.json",
        {"review_packet_hashes": {case["source_id"] + ".json": m.digest(m.canonical(packet))}},
    )
    monkeypatch.setattr(m, "registered_evidence", lambda: evidence)

    def invoke(name: str) -> dict[str, Any]:
        return dict(m.workflow.record_review(m.RUN_ID, name))

    monkeypatch.setattr(m, "invoke_registered", invoke)
    submission = {
        "version": "development-human-submission-v1",
        "batch_id": "batch-001",
        "reviewer_id": REVIEWER,
        "records": [record],
    }
    m.write_new(m.ADMIN / "inbox/fixture.json", submission)
    return qa, submission


def test_registered_recording_is_append_only_idempotent_and_issue_linked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qa, submission = synthetic_store(tmp_path, monkeypatch)
    submission["records"][0]["concerns"] = [
        {
            "category": "EVIDENCE_LOCATOR",
            "description": "Synthetic reported concern",
            "severity_candidate": "LOW",
            "reviewer_note": "Fixture",
        }
    ]
    (m.ADMIN / "inbox/fixture.json").write_bytes(m.canonical(submission))
    result = m.record_submission("fixture.json", qa)
    assert result["substantive_reviews_appended"] == 1
    original = (m.RUN / "fact-review-history.jsonl").read_bytes()
    assert m.record_submission("fixture.json", qa)["substantive_reviews_appended"] == 0
    assert (m.RUN / "fact-review-history.jsonl").read_bytes() == original
    assert len(m.journal(m.ADMIN / "completion-ledger.jsonl")) == 1
    issues = m.journal(m.ADMIN / "development-review-issues.jsonl")
    assert len(issues) == 1 and issues[0]["status"] == "OPEN_FIRST_PASS"
    frame, config = m.frame_and_config(qa)
    _, active, _ = m.history_state(frame, config)
    assert len(active) == 1
    assert not any(
        k in m.journal(m.ADMIN / "completion-ledger.jsonl")[0]
        for k in ["decision", "correct_state", "reason"]
    )


def test_all_records_are_validated_before_any_registered_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qa, submission = synthetic_store(tmp_path, monkeypatch)
    submission["records"][0]["review"]["correct_state"] = "TYPO_STATE"
    (m.ADMIN / "inbox/fixture.json").write_bytes(m.canonical(submission))
    with pytest.raises(ValueError, match="STATE"):
        m.record_submission("fixture.json", qa)
    assert not (m.RUN / "fact-review-history.jsonl").exists()
    assert m.journal(m.ADMIN / "completion-ledger.jsonl") == []


def test_incomplete_pass_does_not_calculate_acceptance_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qa, _ = synthetic_store(tmp_path, monkeypatch)
    frame, config = m.frame_and_config(qa)
    result = m.progress(frame, config)
    assert result["reviewed"] == 0 and result["remaining"] == 718
    assert all(
        x["status"] == "NOT_CALCULATED_FIRST_PASS_INCOMPLETE" for x in result["metrics"].values()
    )
    assert result["full_extraction_authorized"] is False


def test_control_forms_never_load_proposals_or_create_candidate_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(m, "ADMIN", tmp_path / "admin")
    monkeypatch.setattr(m, "RUN", tmp_path / "synthetic-run")
    m.ADMIN.mkdir()
    frame = {"records": m.batches({"selected": [row(), row(1, "PARSER_CANDIDATE")]})}
    name = frame["records"][0]["source_id"] + ".control.html"
    m.workflow.sec.write_new(m.RUN / "packets" / name, b"Synthetic source control page")
    m.write_new(
        m.RUN / "run-manifest.json",
        {"review_packet_hashes": {name: m.digest(b"Synthetic source control page")}},
    )
    monkeypatch.setattr(m, "history_state", lambda *_: ({}, {}, []))
    monkeypatch.setattr(
        m, "packet_for", lambda *_: pytest.fail("Control rendering must not read proposals")
    )
    m.render_views(frame, {"reviewer_id": REVIEWER})
    page = (m.ADMIN / "batches/batch-001.html").read_text("utf-8")
    assert ".control.html" in page and 'data-role="PARSER_CANDIDATE"' not in page
    assert 'name="event_id"' not in page and "confidence" not in page
    assert "selected=" not in page and " checked" not in page
    assert frame["records"][1]["source_id"] not in page
    assert "connect-src" in page and "Export completed human reviews" in page


@pytest.mark.parametrize(
    "probe",
    [
        "__import__('yfinance')",
        "__import__('wrds')",
        "socket.create_connection(('www.sec.gov',443))",
        "subprocess.run([sys.executable,'-c','pass'])",
        "Path('C:/outside/not-registered.json').read_bytes()",
        "sqlite3.connect(':memory:')",
    ],
)
def test_admin_boundary_denies_network_vendor_process_file_and_database(probe: str) -> None:
    code = (
        "import runpy,sys,socket,subprocess,sqlite3;from pathlib import Path;"
        + f"m=runpy.run_path({str(ENTRY)!r},run_name='probe');m['install_guard']();"
        + probe
    )
    done = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code], capture_output=True, text=True, check=False
    )
    assert done.returncode != 0 and "DENIED" in done.stderr


def test_exact_registered_subprocess_is_allowed_by_windows_guard(
    tmp_path: Path,
) -> None:
    script = tmp_path / "tools/corporate_events/parser_qa.py"
    script.parent.mkdir(parents=True)
    script.write_text("raise SystemExit(2)\n", encoding="utf-8")
    code = (
        "import runpy;from pathlib import Path;"
        + f"m=runpy.run_path({str(ENTRY)!r},run_name='probe');"
        + f"m['invoke_registered'].__globals__['ROOT']=Path({str(tmp_path)!r});"
        + "m['install_guard']();"
        + "m['invoke_registered']('human-'+('a'*64)+'.json')"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code], capture_output=True, text=True, check=False
    )
    assert done.returncode != 0
    assert "REGISTERED_REVIEW_COMMAND_REJECTED_INPUT" in done.stderr
    assert "REVIEW_ADMIN_PROCESS_DENIED" not in done.stderr


@pytest.mark.parametrize("mismatch", ["selection", "run_copy", "report_binding"])
def test_frozen_identity_failure_stops_without_reconstruction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    pilot = tmp_path / "pilot"
    run = pilot / "runs" / m.RUN_ID
    protocol = tmp_path / "protocol"
    run.mkdir(parents=True)
    protocol.mkdir()
    monkeypatch.setattr(m, "PILOT", pilot)
    monkeypatch.setattr(m, "RUN", run)
    monkeypatch.setattr(m, "PROTOCOL", protocol)
    monkeypatch.setattr(m, "EXPECTED_RECORDS", 1)
    monkeypatch.setattr(m, "EXPECTED_CELLS", 1)
    qa = {"selected": [row()], "cells": [{}], "final_qa_sample": False}
    raw = m.canonical(qa)
    sha = m.digest(raw)
    monkeypatch.setattr(m, "EXPECTED_SHA", sha)
    m.write_new(pilot / "frozen-development-qa-selection.json", qa)
    m.write_new(run / "development-qa-selection.json", qa)
    report = {
        "run_id": m.RUN_ID,
        "frozen_development_qa_sha256": sha,
        "qa_selected_records": 1,
        "qa_cells": 1,
        "protocol": {"parser_code_hashes": {}},
    }
    m.write_new(run / "parser-development-report.json", report)
    m.write_new(protocol / "parser-qa-extension.json", {"files": {}})
    assert m.verify_frozen() == qa
    if mismatch == "selection":
        (pilot / "frozen-development-qa-selection.json").write_bytes(raw + b" ")
    elif mismatch == "run_copy":
        (run / "development-qa-selection.json").write_bytes(raw + b" ")
    else:
        report["frozen_development_qa_sha256"] = "0" * 64
        (run / "parser-development-report.json").write_bytes(m.canonical(report))
    with pytest.raises(ValueError, match="FROZEN_DEVELOPMENT_QA_IDENTITY_FAILURE"):
        m.verify_frozen()
    assert not (pilot / "human-review-v1").exists()


def test_interrupted_receipt_recording_recovers_without_second_human_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qa, _ = synthetic_store(tmp_path, monkeypatch)
    original = m.append_receipt

    def fail_once(value: dict[str, Any]) -> None:
        raise OSError("Synthetic interrupted administrative receipt")

    monkeypatch.setattr(m, "append_receipt", fail_once)
    with pytest.raises(OSError):
        m.record_submission("fixture.json", qa)
    history = (m.RUN / "fact-review-history.jsonl").read_bytes()
    assert len(m.journal(m.RUN / "fact-review-history.jsonl")) == 1
    monkeypatch.setattr(m, "append_receipt", original)
    result = m.record_submission("fixture.json", qa)
    assert result["substantive_reviews_appended"] == 0
    assert (m.RUN / "fact-review-history.jsonl").read_bytes() == history
    assert len(m.journal(m.ADMIN / "completion-ledger.jsonl")) == 1


def test_human_changed_decision_appends_supersession_without_replacing_prior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qa, submission = synthetic_store(tmp_path, monkeypatch)
    m.record_submission("fixture.json", qa)
    before = (m.RUN / "fact-review-history.jsonl").read_bytes()
    previous = m.journal(m.RUN / "fact-review-history.jsonl")[-1]
    human = submission["records"][0]
    human["review"].update(
        decision="ACCEPTED",
        correct_state="NEW_AUTHORIZATION",
        event_presence=True,
        supersedes_review_id=previous["review_id"],
        reason="Synthetic human correction",
    )
    human["first_pass_status"] = "REVIEWED"
    m.write_new(m.ADMIN / "inbox/correction.json", submission)
    m.record_submission("correction.json", qa)
    assert (m.RUN / "fact-review-history.jsonl").read_bytes().startswith(before)
    history = m.journal(m.RUN / "fact-review-history.jsonl")
    assert len(history) == 2 and history[-1]["supersedes_review_id"] == previous["review_id"]
    frame, config = m.frame_and_config(qa)
    _, active, _ = m.history_state(frame, config)
    assert len(active) == 1 and next(iter(active.values()))["review_status"] == "REVIEWED"


def test_ledger_tampering_is_not_counted_as_human_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qa, _ = synthetic_store(tmp_path, monkeypatch)
    m.record_submission("fixture.json", qa)
    entries = m.journal(m.ADMIN / "completion-ledger.jsonl")
    entries[0]["review_status"] = "REVIEWED"
    (m.ADMIN / "completion-ledger.jsonl").write_bytes(m.canonical(entries[0]))
    frame, config = m.frame_and_config(qa)
    with pytest.raises(ValueError, match="COMPLETION_LEDGER_CHAIN_INVALID"):
        m.history_state(frame, config)


@pytest.mark.parametrize("mode", ["blank", "complete", "no_attestation", "no_evidence"])
def test_offline_export_requires_explicit_human_decisions_and_attestation(
    tmp_path: Path,
    mode: str,
) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node unavailable for offline form check")
    harness = r"""
let captured=null,clicked=false;
const mode=MODE;
const fields={};
for(const name of ['decision','correct_state','presence','first_pass_status','reason',
                  'issue_description','severity','reviewer_note','supersedes'])
 fields[name]={value:''};
fields.evidence={value:'',selectedOptions:[]};fields.attest={checked:false};
if(mode!=='blank'){
 Object.assign(fields.decision,{value:'AMBIGUOUS'});
 fields.correct_state.value='UNRESOLVED';fields.presence.value='UNKNOWN';
 fields.first_pass_status.value='REVIEWED_UNRESOLVED';
 fields.reason.value='Synthetic user-entered fixture only';
 fields.attest.checked=mode!=='no_attestation';
 fields.evidence.selectedOptions=mode==='no_evidence'?[]:[{value:'proof-fixture'}];
}
const card={dataset:{id:'case-fixture',source:'source-fixture',family:'SHARE_REPURCHASE',
                    role:'INDEPENDENT_SOURCE_CONTROL'},
 querySelector(selector){return fields[selector.match(/name="([^"]+)"/)[1]];},
 querySelectorAll(selector){return selector==='select,textarea'?
  Object.values(fields).filter(x=>Object.hasOwn(x,'value')):[];}};
const elements={config:{textContent:JSON.stringify({batch_id:'batch-001',
 reviewer_id:'SYNTHETIC_TEST_HUMAN'})},
 export:{},message:{textContent:''}};
global.document={getElementById(id){return elements[id];},querySelectorAll(){return [card];},
 createElement(){return {click(){clicked=true;}};}};
global.Blob=class{constructor(parts){this.data=parts.join('');}};
global.URL={createObjectURL(blob){captured=JSON.parse(blob.data);return 'blob:synthetic';}};
"""
    harness = harness.replace("MODE", json.dumps(mode))
    tail = (
        "document.getElementById('export').onclick();"
        "console.log(JSON.stringify({captured,clicked,message:elements.message.textContent}));"
    )
    script = tmp_path / "synthetic-form.js"
    script.write_text(harness + m.FORM_SCRIPT + tail, encoding="utf-8")
    done = subprocess.run([node, str(script)], capture_output=True, text=True, check=True)
    result = json.loads(done.stdout)
    if mode == "complete":
        item = result["captured"]["records"][0]
        assert result["clicked"] and item["human_attested"] is True
        assert item["review"]["event_presence"] is None
        assert item["review"]["correct_state"] == "UNRESOLVED"
        assert item["review"]["event_id"] is None
    else:
        assert result["captured"] is None and not result["clicked"]


def test_completed_synthetic_pass_reports_supported_rates_but_diagnosis_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qa, _ = synthetic_store(tmp_path, monkeypatch)
    m.record_submission("fixture.json", qa)
    monkeypatch.setattr(m, "EXPECTED_RECORDS", 1)
    frame, config = m.frame_and_config(qa)
    result = m.progress(frame, config)
    assert result["human_first_pass_complete"] is True
    assert result["status"] == "FIRST_PASS_REVIEW_COMPLETE_DIAGNOSIS_PENDING"
    assert result["metrics"]["ambiguity_unresolved_rate"]["value"] == 1
    assert result["metrics"]["fact_extraction_correctness"]["status"] == "NOT_MEASURED"
    assert (
        result["metrics"]["controlled_state_agreement"]["status"]
        == "NOT_MEASURED_EMPTY_DENOMINATOR"
    )
    assert result["metrics"]["recall"]["status"] == "NOT_MEASURED"
    assert result["full_extraction_authorized"] is False
