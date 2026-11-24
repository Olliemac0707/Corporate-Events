"""Audit frozen development evidence offline; never authorize research extraction."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path, PureWindowsPath
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "reports/corporate-event-alpha-v1/phase2-private"
PROTOCOL = ROOT / "protocols/corporate-event-alpha-v1"
REGISTERED = (
    "tools/corporate_events/infrastructure_audit.py",
    "tests/test_infrastructure_audit.py",
    "protocols/corporate-event-alpha-v1/infrastructure-audit-policy-v1.json",
)
SAMPLE_SHA = "5da0f6c6061ebea6ac08e57e598e670636d634dd7b1dc158767fba135be073ae"
EVIDENCE_SHA = "9fc232215cc09e1a332f9992e5f617af3ec144ae7eb56cef9519a3b27a9b8f99"


def offline_guard(event: str, args: tuple[Any, ...]) -> None:
    del args
    if event.startswith(("socket.", "http.client.")):
        raise RuntimeError("INFRASTRUCTURE_AUDIT_NETWORK_DENIED")


def safe_file(base: Path, name: str) -> Path:
    if (
        not isinstance(name, str)
        or not name
        or "\\" in name
        or ":" in name
        or PureWindowsPath(name).is_absolute()
        or any(part in {"", ".", ".."} for part in name.split("/"))
    ):
        raise ValueError("MANIFEST_PATH_INVALID")
    resolved = (base / name).resolve(strict=True)
    if not resolved.is_relative_to(base.resolve()) or not resolved.is_file():
        raise ValueError("MANIFEST_PATH_OUTSIDE_BASE_OR_NOT_FILE")
    return resolved


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            result.update(block)
    return result.hexdigest()


def read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("JSON_OBJECT_REQUIRED")
    return value


def verify_files(base: Path, records: dict[str, str]) -> int:
    if not isinstance(records, dict) or not records:
        raise ValueError("EMPTY_OR_INVALID_MANIFEST")
    for name, expected in records.items():
        if not isinstance(expected, str) or not re.fullmatch("[0-9a-f]{64}", expected):
            raise ValueError("MANIFEST_HASH_INVALID")
        if digest(safe_file(base, name)) != expected:
            raise ValueError("MANIFEST_HASH_MISMATCH")
    return len(records)


def requirement_backlog(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) != 69:
        raise ValueError("FROZEN_CANDIDATE_COUNT_CHANGED")
    identifiers = [x["proposal_id"] for x in rows]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("DUPLICATE_CANDIDATE_ID")
    requirements: Counter[str] = Counter()
    for row in rows:
        if not re.fullmatch("[0-9a-f]{64}", row["proposal_id"]):
            raise ValueError("CANDIDATE_ID_INVALID")
        for key in (
            "public_dissemination_certified",
            "independent_full_source_QA_complete",
            "research_event_accepted",
        ):
            if row.get(key) is not False:
                raise ValueError("FROZEN_UNRESOLVED_GATE_CHANGED")
        if row.get("frozen_facts_unchanged") is not True:
            raise ValueError("FROZEN_FACTS_CHANGED")
        required = row["required_evidence"]
        if (
            not isinstance(required, list)
            or not required
            or any(not isinstance(x, str) or not re.fullmatch("[A-Z_]+", x) for x in required)
            or len(required) != len(set(required))
        ):
            raise ValueError("EVIDENCE_REQUIREMENTS_INVALID")
        requirements.update(required)
    return {
        "provisional_mentions": len(rows),
        "research_accepted_events": 0,
        "unresolved_requirement_counts": dict(sorted(requirements.items())),
        "counts_are_requirements_not_unique_events_or_accuracy_metrics": True,
        "public_dissemination_certified": 0,
        "independent_full_source_QA_complete": 0,
        "full_extraction_authorized": False,
        "final_qa_authorized": False,
        "phase3_authorized": False,
    }


def legacy_snapshot(records: dict[str, str]) -> dict[str, str]:
    """Adapt preserved Windows snapshot keys; normal path checks still apply."""
    normalized: dict[str, str] = {}
    for name, value in records.items():
        key = name.replace("\\", "/")
        if key in normalized and normalized[key] != value:
            raise ValueError("LEGACY_PATH_NORMALIZATION_COLLISION")
        normalized[key] = value
    return normalized


def run(run_id: str) -> dict[str, Any]:
    if not re.fullmatch("offline-infrastructure-[0-9]{3}", run_id):
        raise ValueError("IMMUTABLE_RUN_ID_INVALID")
    target = PRIVATE / "infrastructure-audit-v1" / run_id
    if target.exists():
        raise ValueError("IMMUTABLE_AUDIT_RUN_ALREADY_EXISTS")
    sys.addaudithook(offline_guard)
    extension = read(PROTOCOL / "infrastructure-audit-extension-v1.json")
    if set(extension["files"]) != set(REGISTERED):
        raise ValueError("AUDIT_REGISTRY_SCOPE_INVALID")
    verified = verify_files(ROOT, extension["files"])
    policy = read(PROTOCOL / "infrastructure-audit-policy-v1.json")
    if policy["evidence_manifest_sha256"] != EVIDENCE_SHA:
        raise ValueError("AUDIT_EVIDENCE_PIN_CHANGED")
    for key in ("network_allowed", "market_data_allowed", "gate_opening_allowed"):
        if policy.get(key) is not False:
            raise ValueError("AUDIT_CLOSED_SCOPE_REQUIRED")
    for name in (
        "parser-development-v2-extension.json",
        "parser-development-v3-extension.json",
        "parser-development-v4-extension.json",
        "authority-evidence-extension-v1.json",
        "authority-evidence-extension-v2.json",
        "offline-feed-prefix-verifier-extension-v1.json",
    ):
        verified += verify_files(ROOT, read(PROTOCOL / name)["files"])
    path = ROOT / "tools/corporate_events/parser_development_v4.py"
    spec = importlib.util.spec_from_file_location("infrastructure_frozen_baseline", path)
    assert spec and spec.loader
    baseline: Any = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = baseline
    spec.loader.exec_module(baseline)
    _, _, proof = baseline.review.preflight()
    evidence = PRIVATE / "authority-evidence-v2"
    if digest(evidence / "evidence-manifest.json") != EVIDENCE_SHA:
        raise ValueError("PINNED_EVIDENCE_MANIFEST_CHANGED")
    manifest = read(evidence / "evidence-manifest.json")
    verified += verify_files(evidence, manifest["files"])
    verified += verify_files(PRIVATE, manifest["archive_diagnostic_files"])
    verified += verify_files(ROOT, manifest["supporting_private_tools"])
    if digest(PRIVATE / "authority-evidence-v1/evidence-manifest.json") != manifest[
        "prior_v1_manifest_sha256"
    ]:
        raise ValueError("PRIOR_EVIDENCE_MANIFEST_CHANGED")
    v1 = PRIVATE / "authority-evidence-v1"
    old = read(v1 / "evidence-manifest.json")
    verified += verify_files(v1, old["files"])
    verified += verify_files(v1 / "inspected-authorities", old["source_documents"])
    verified += verify_files(
        v1,
        {"objects/sha256/" + h[:2] + "/" + h: h for h in old["source_objects"].values()},
    )
    prior_preflights = sorted((v1 / "acquisitions").glob("*/preflight.json"))
    if not prior_preflights:
        raise ValueError("PRIOR_BASELINE_SNAPSHOT_MISSING")
    for prior in prior_preflights:
        verified += verify_files(ROOT, legacy_snapshot(read(prior)["baseline_snapshot"]))
    v4 = PRIVATE / "parser-development-v4"
    if digest(v4 / "checkpoint-artifact-manifest.json") != manifest[
        "prior_v4_checkpoint_manifest_sha256"
    ]:
        raise ValueError("PRIOR_PARSER_CHECKPOINT_CHANGED")
    verified += verify_files(v4, read(v4 / "checkpoint-artifact-manifest.json")["files"])
    sample = PRIVATE / "parser-qa-v1/runs/development-parse-v2/development-qa-selection.json"
    if digest(sample) != SAMPLE_SHA:
        raise ValueError("FROZEN_SAMPLE_CHANGED")
    frame = read(PRIVATE / "parser-qa-v1/human-review-v1/batch-manifest.json")
    cells = Counter(tuple(x["qa_cell_identity"]) for x in frame["records"])
    if len(frame["records"]) != 718 or len(cells) != 297 or max(cells.values()) != 5:
        raise ValueError("FROZEN_SAMPLE_COUNTS_CHANGED")
    controls = [x for x in frame["records"] if x["role"] == "INDEPENDENT_SOURCE_CONTROL"]
    reader = read(evidence / "source-only-reader/manifest.json")["controls"]
    if len(controls) != 627 or len(reader) != 627:
        raise ValueError("SOURCE_CONTROL_COUNT_CHANGED")
    for case, page in zip(controls, reader, strict=True):
        if case["sample_record_id"] != page["sample_record_id"]:
            raise ValueError("SOURCE_CONTROL_ORDER_CHANGED")
        h = case["source_document_sha256"]
        verified += verify_files(
            PRIVATE / "parser-qa-v1/runs/development-parse-v2/documents", {h + ".txt": h}
        )
    backlog = requirement_backlog(read(evidence / "candidate-gate-ledger.json")["records"])
    result = {
        "status": "INFRASTRUCTURE_INTEGRITY_PASS_RESEARCH_GATES_CLOSED",
        "run_id": run_id,
        "protocol_preflight": proof,
        "verified_file_hashes": verified,
        "pinned_evidence_manifest_sha256": EVIDENCE_SHA,
        "sample_records": 718,
        "sample_cells": 297,
        "sample_changes": 0,
        "backlog": backlog,
        "network_requests": 0,
        "new_filings": 0,
        "new_human_decisions": 0,
        "parser_changes": 0,
        "firewall_counters": dict.fromkeys(baseline.review.workflow.sec.FIREWALL_COUNTERS, 0),
        "not_an_independent_scientific_QA_certification": True,
    }
    # All validation precedes output creation; partial/failed checks never produce a PASS.
    target.mkdir(parents=True, exist_ok=False)
    with (target / "audit.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    with (target / "artifact-manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(
            {"files": {"audit.json": digest(target / "audit.json")}, "code_hashes": extension},
            stream,
            indent=2,
        )
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--run-id", required=True)
    try:
        print(json.dumps(run(cli.parse_args().run_id)), flush=True)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as error:
        print(json.dumps({"status": "AUDIT_FAILED_CLOSED", "reason": str(error)}), flush=True)
        raise SystemExit(2) from None

