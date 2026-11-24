"""Fetch only exact planned SEC evidence objects; never alter the frozen pilot."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import ssl
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "protocols/corporate-event-alpha-v1"
PRIVATE = ROOT / "reports/corporate-event-alpha-v1/phase2-private"
OUTPUT = PRIVATE / "authority-evidence-v2"
REGISTERED = (
    "tools/corporate_events/sec_authority_client.py",
    "tools/corporate_events/authority_evidence_v2.py",
    "tests/test_authority_evidence_v2.py",
    "protocols/corporate-event-alpha-v1/authority-evidence-policy-v2.json",
)


def load(name: str, path: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


baseline = load("evidence_baseline_v4", "tools/corporate_events/parser_development_v4.py")
documents = load("evidence_documents", "tools/corporate_events/sec_authority_client.py")
sec = baseline.review.workflow.sec


def check_plan(plan: dict[str, Any]) -> None:
    if plan.get("version") != "authority-evidence-plan-v2":
        raise ValueError("EVIDENCE_PLAN_VERSION_INVALID")
    entries = plan["entries"]
    if not 0 < len(entries) <= 8 or len({x["url"] for x in entries}) != len(entries):
        raise ValueError("EVIDENCE_PLAN_SIZE_OR_DUPLICATE_INVALID")
    if sum(x["kind"] == "COMPLETE_SUBMISSION" for x in entries) > 3:
        raise ValueError("EVIDENCE_FILING_CAP_EXCEEDED")
    if plan["max_received_body_bytes"] != 67108864:
        raise ValueError("EVIDENCE_BYTE_CAP_IDENTITY_INVALID")
    for flag in (
        "market_data_allowed",
        "full_extraction_authorized",
        "final_qa_authorized",
        "phase3_authorized",
        "sample_changes_allowed",
        "parser_changes_allowed",
    ):
        if plan.get(flag) is not False:
            raise ValueError("EVIDENCE_CLOSED_GATE_REQUIRED:" + flag)
    allowed = {x["url"] for x in entries}
    allowed.update(edge[1] for edge in plan["redirects"])
    for item in entries:
        documents.validate_url(item["url"], allowed)
        if item["kind"] == "COMPLETE_SUBMISSION":
            if not re.fullmatch(
                r"https://www.sec.gov/Archives/edgar/data/[0-9]{1,10}/"
                r"[0-9]{10}-[0-9]{2}-[0-9]{6}\.txt",
                item["url"],
            ):
                raise ValueError("EVIDENCE_SUBMISSION_NOT_COMPLETE_REGISTERED_PATH")
        elif item["kind"] != "OFFICIAL_AUTHORITY":
            raise ValueError("EVIDENCE_KIND_INVALID")
        elif "/Archives/" in item["url"]:
            raise ValueError("AUTHORITY_CANNOT_HIDE_FILING_OR_ARCHIVE")
    for first, second in plan["redirects"]:
        if first not in allowed or second not in allowed:
            raise ValueError("EVIDENCE_REDIRECT_UNREGISTERED")
        documents.validate_url(first, allowed)
        documents.validate_url(second, allowed)
        if first == second:
            raise ValueError("EVIDENCE_REDIRECT_LOOP")


def check_object_budget(url: str, registry: list[dict[str, Any]]) -> None:
    known = {x["url"] for x in registry}
    if url in known:
        return
    if len(known) >= 8:
        raise ValueError("EVIDENCE_GLOBAL_OBJECT_BUDGET_EXHAUSTED")
    filing_urls = {u for u in known if "/Archives/edgar/data/" in u and u.endswith(".txt")}
    if "/Archives/edgar/data/" in url and len(filing_urls) >= 3:
        raise ValueError("EVIDENCE_GLOBAL_FILING_BUDGET_EXHAUSTED")


def run(plan_name: str) -> dict[str, Any]:
    if plan_name not in {"authority-documents-plan.json", "bounded-historical-filings-plan.json"}:
        raise ValueError("EVIDENCE_PLAN_NAME_INVALID")
    proof = baseline.review.preflight()
    prior_extension = baseline.review.read(PROTOCOL / "authority-evidence-extension-v1.json")
    for old_name, old_sha in prior_extension["files"].items():
        if sec.sha((ROOT / old_name).read_bytes()) != old_sha:
            raise ValueError("EVIDENCE_V1_REGISTERED_CODE_CHANGED")
    ext = baseline.review.read(PROTOCOL / "authority-evidence-extension-v2.json")
    if set(ext["files"]) != set(REGISTERED):
        raise ValueError("EVIDENCE_EXTENSION_SCOPE_INVALID")
    for name, sha in ext["files"].items():
        if sec.sha((ROOT / name).read_bytes()) != sha:
            raise ValueError("EVIDENCE_REGISTERED_CODE_CHANGED")
    prior: dict[str, str] = baseline.unchanged_snapshot()
    for v in ("v2", "v3", "v4"):
        e = baseline.review.read(PROTOCOL / ("parser-development-" + v + "-extension.json"))
        for name, sha in e["files"].items():
            if sec.sha((ROOT / name).read_bytes()) != sha:
                raise ValueError("EVIDENCE_PRIOR_PARSER_CHANGED")
            prior[name] = sha
        sec.EXTENSION_FILES = (*sec.EXTENSION_FILES, *e["files"])
    plan_path = OUTPUT / plan_name
    plan = baseline.review.read(plan_path)
    check_plan(plan)
    required_kind = (
        "OFFICIAL_AUTHORITY"
        if plan_name == "authority-documents-plan.json"
        else "COMPLETE_SUBMISSION"
    )
    if any(x["kind"] != required_kind for x in plan["entries"]):
        raise ValueError("EVIDENCE_PLAN_KIND_SCOPE_INVALID")
    plan_sha = sec.sha(plan_path.read_bytes())
    agent = sec.private_contact(ROOT)
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    sec.EXTENSION_FILES = (*sec.EXTENSION_FILES, *REGISTERED)
    sec.install_firewall()
    target = OUTPUT / "acquisitions" / plan_sha[:16]
    target.mkdir(parents=True, exist_ok=True)
    if not (target / "plan.json").exists():
        sec.write_new(target / "plan.json", plan_path.read_bytes())
        sec.write_new(
            target / "preflight.json",
            sec.json_bytes(
                {
                    "protocol": proof[2],
                    "contact_status": "VALID_PRIVATE_CONTACT",
                    "registered_files": ext["files"],
                    "baseline_snapshot": prior,
                    "plan_sha256": plan_sha,
                    "no_contact_value_logged": True,
                }
            ),
        )
    allowed = {x["url"] for x in plan["entries"]} | {x[1] for x in plan["redirects"]}
    results = []
    blocker = None
    with sec.Scheduler(PRIVATE) as scheduler:
        # Both plans share a total received-byte budget and raw-object namespace.
        budget = documents.ByteBudget(
            OUTPUT / "download-budget.json", 67108864, "authority-evidence-v2"
        )
        transport = documents.HttpsDocuments(sec, allowed, context, budget)
        client = documents.DocumentClient(
            sec,
            OUTPUT,
            target,
            scheduler,
            transport,
            allowed,
            agent,
            time.time() + 1800,
            {tuple(x) for x in plan["redirects"]},
        )
        for item in plan["entries"]:
            try:
                registry_path = OUTPUT / "object-versions.jsonl"
                registry = (
                    [json.loads(x) for x in registry_path.read_bytes().splitlines()]
                    if registry_path.exists()
                    else []
                )
                check_object_budget(item["url"], registry)
                raw, cached = client.fetch(item["url"])
                results.append(
                    {
                        "url": item["url"],
                        "purpose": item["purpose"],
                        "status": "CACHED",
                        "sha256": cached["sha256"],
                        "bytes": len(raw),
                    }
                )
            except (sec.AcquisitionError, ValueError) as error:
                reason = str(error)
                results.append({"url": item["url"], "status": "UNRESOLVED", "reason": reason})
                if any(
                    x in reason
                    for x in (
                        "403",
                        "429",
                        "DEFERRED",
                        "FIREWALL",
                        "BLOCKED",
                        "BUDGET",
                        "HASH_MISMATCH",
                    )
                ):
                    blocker = reason
                    break
    for name, sha in prior.items():
        if sec.sha((ROOT / name).read_bytes()) != sha:
            raise ValueError("EVIDENCE_BASELINE_CHANGED")
    result = {
        "status": "STOPPED_BOUNDED_EVIDENCE_ACQUISITION"
        if blocker
        else "COMPLETE_BOUNDED_EVIDENCE_ACQUISITION"
        if all(x["status"] == "CACHED" for x in results)
        else "COMPLETE_BOUNDED_ATTEMPT_WITH_GAPS",
        "blocker": blocker,
        "plan_sha256": plan_sha,
        "results": results,
        "received_body_bytes_total": budget.spent,
        "body_byte_cap": budget.limit,
        "frozen_pilot_and_parsers_unchanged": True,
        "contact_value_logged": False,
        "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
        "full_extraction_authorized": False,
        "final_qa_authorized": False,
        "phase3_authorized": False,
    }
    sec.append_record(target / "completion-history.jsonl", result)
    print(json.dumps(result), flush=True)
    return result


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("--plan", required=True)
    run(args.parse_args().plan)
