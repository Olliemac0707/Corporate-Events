"""Build the fixed, offline repurchase ledger with source evidence and closed QA gates."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from html import escape
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "protocols/corporate-event-alpha-v1"
PRIVATE = ROOT / "reports/corporate-event-alpha-v1/phase2-private"
OUTPUT = PRIVATE / "bounded-repurchase-ledger-v2"
ACQUIRED_SHA = "5014ffc3d51f479af445a5b79e729ae853ec6c27d3b7d387e37269c003acda9b"
AUDIT_SHA = "58672f814048c6a41fb8046594f49a0ef2eff8d55e33e7f475199292b43b09a7"
REGISTERED = {
    "tools/corporate_events/bounded_repurchase_rules_v2.py",
    "tools/corporate_events/bounded_repurchase_ledger_v2.py",
    "tests/test_bounded_repurchase_ledger_v2.py",
    "protocols/corporate-event-alpha-v1/bounded-repurchase-ledger-policy-v2.json",
}


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools/corporate_events" / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


acq = load("ledger_acquisition", "repurchase_acquisition_v2.py")
rules = load("ledger_rules", "bounded_repurchase_rules_v2.py")
sec = acq.sec


def preflight() -> tuple[dict[str, Any], dict[str, Any]]:
    source, proof = acq.preflight()
    registry = json.loads((PROTOCOL / "bounded-repurchase-ledger-extension-v2.json").read_bytes())
    if set(registry["files"]) != REGISTERED:
        raise ValueError("LEDGER_REGISTRY_SCOPE_MISMATCH")
    for name, expected in registry["files"].items():
        if sec.file_sha(ROOT / name) != expected:
            raise ValueError("LEDGER_REGISTERED_CODE_CHANGED")
    previous = json.loads((PROTOCOL / "bounded-repurchase-ledger-extension-v1.json").read_bytes())
    for name, expected in previous["files"].items():
        if sec.file_sha(ROOT / name) != expected:
            raise ValueError("FIRST_LEDGER_VERSION_CHANGED")
    if (
        sec.file_sha(PRIVATE / "bounded-repurchase-ledger-v1/output-manifest.json")
        != "476c44bbc1c24d1ee044eaa39746124e718e267ee4aaa41c28d05c5b8f238fd0"
    ):
        raise ValueError("FIRST_LEDGER_OUTPUT_CHANGED")
    parser_files = {}
    for filename in (
        "parser-qa-extension.json",
        "parser-development-v2-extension.json",
        "parser-development-v4-extension.json",
    ):
        predecessor = json.loads((PROTOCOL / filename).read_bytes())
        for name, expected in predecessor["files"].items():
            if sec.file_sha(ROOT / name) != expected:
                raise ValueError("FROZEN_PARSER_CHANGED")
        parser_files.update(predecessor["files"])
    path = acq.OUTPUT / "acquired-source-manifest.json"
    if (
        sec.file_sha(path) != ACQUIRED_SHA
        or sec.file_sha(acq.OUTPUT / "acquisition-audit.json") != AUDIT_SHA
    ):
        raise ValueError("FROZEN_ACQUISITION_CHANGED")
    audit = json.loads((acq.OUTPUT / "acquisition-audit.json").read_bytes())
    inventory = json.loads(path.read_bytes())
    if (
        audit["status"] != "ALL_100_RAW_SUBMISSIONS_REPLAY_PASSED"
        or audit["acquired_manifest_sha256"] != ACQUIRED_SHA
    ):
        raise ValueError("ACQUISITION_AUDIT_NOT_PASSED")
    if {x["accession"] for x in source["entries"]} != {
        x["accession"] for x in inventory["sources"]
    } or len(inventory["sources"]) != 100:
        raise ValueError("EXACT_100_SOURCE_MEMBERSHIP_REQUIRED")
    return inventory, {
        "acquisition": proof,
        "registry": registry,
        "parser_files": parser_files,
        "original_718_sample_sha256": (
            "5da0f6c6061ebea6ac08e57e598e670636d634dd7b1dc158767fba135be073ae"
        ),
    }


def offline_guard(proof: dict[str, Any]) -> None:
    sec.EXTENSION_FILES = (*sec.EXTENSION_FILES, *REGISTERED, *proof["parser_files"])
    sec.install_firewall()
    sys.addaudithook(
        lambda event, args: (
            (_ for _ in ()).throw(RuntimeError("BOUNDED_LEDGER_NETWORK_AND_PROCESS_DENIED"))
            if event.startswith(("socket.", "http.client.", "subprocess."))
            else None
        )
    )


def immutable(path: Path, raw: bytes) -> None:
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("IMMUTABLE_LEDGER_OUTPUT_CONFLICT")
    else:
        sec.write_new(path, raw)


def write_json(name: str, value: Any) -> None:
    immutable(OUTPUT / name, sec.json_bytes(value))


def verify_evidence(row: dict[str, Any], component: dict[str, Any], text: str) -> None:
    ev = row["evidence"]
    quote = text[ev["text_start"] : ev["text_end"]]
    if (
        not 0 <= ev["text_start"] < ev["text_end"] <= len(text)
        or quote != ev["quote"]
        or sec.sha(quote.encode()) != ev["quote_sha256"]
        or sec.sha(text.encode()) != ev["derived_text_sha256"]
        or ev["source_document_sha256"] != component["raw_document_sha256"]
        or row["accession"] != component["accession"]
        or row["container_sha256"] != component["container_sha256"]
        or ev["container_byte_start"] != component["container_byte_start"]
        or ev["container_byte_end"] != component["container_byte_end"]
        or row["certified_qualifying"] is not False
        or row["source_filing_date_is_public_time"] is not False
    ):
        raise ValueError("LEDGER_EVIDENCE_OR_WITHHOLDING_REPLAY_MISMATCH")


def readable_report(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    tables = []
    for category, label in (
        ("CANDIDATE", "Possible authorizations and increases"),
        ("UNRESOLVED", "Mentions requiring context"),
        ("CONTEXT", "Historical or routine activity"),
        ("REJECTION_CONTEXT", "Other meanings or transaction types"),
    ):
        rendered = []
        for row in rows:
            if row["category"] != category:
                continue
            facts = row["facts"]
            rendered.append(
                "<tr><td>"
                + escape(row["reporting_name_as_indexed"])
                + "<br>Filed "
                + escape(row["source_filed"])
                + "<br>"
                + escape(row["accession"])
                + "</td><td>"
                + escape(row["reason"])
                + "<br>Approval: "
                + escape(str(facts.get("approval_date") or "unresolved"))
                + "<br>Stated announcement: "
                + escape(str(facts.get("stated_announcement_date") or "unresolved"))
                + "</td><td>"
                + escape(row["evidence"]["quote"])
                + '<br><a href="'
                + escape(row["derived_text_file"], quote=True)
                + '">Full extracted component text</a></td><td>'
                + escape("; ".join(row["unresolved_requirements"]))
                + "</td></tr>"
            )
        tables.append(
            "<details"
            + (" open" if category == "CANDIDATE" else "")
            + "><summary>"
            + label
            + " ("
            + str(len(rendered))
            + ")</summary><table><thead><tr><th>Source filer</th>"
            "<th>Proposed interpretation</th><th>Source passage</th>"
            "<th>Unresolved checks</th></tr></thead><tbody>"
            + "".join(rendered)
            + "</tbody></table></details>"
        )
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'\">"
        "<title>Bounded repurchase development ledger</title><style>"
        "body{font:16px/1.5 system-ui;margin:30px;max-width:1600px}"
        "table{border-collapse:collapse;width:100%}"
        "td,th{padding:12px;border:1px solid #ccc;text-align:left;vertical-align:top}"
        "td:nth-child(3){width:45%}"
        "details{margin:24px 0}summary{font-size:1.2em;font-weight:bold;cursor:pointer}</style>"
        "<h1>Repurchase development ledger</h1><p>This list organizes source passages from "
        + str(summary["source_filings"])
        + " fixed filings. Candidate passages are possible events requiring checks; "
        "they are not verified or necessarily distinct events. "
        "No returns or regressions have been run.</p>"
        "<p>Filing dates, approval dates and stated announcement dates remain separate. "
        "Public release times and economic-action deduplication are unresolved. "
        "This view includes predictions and is not a blinded review packet.</p>"
        "<p>All original source bytes remain in the private acquisition cache. "
        "Unparsed components and limits are recorded in source-coverage.json; "
        "an empty keyword result does not establish event absence.</p>"
        + "".join(tables)
        + "</html>"
    )


def build() -> dict[str, Any]:
    inventory, proof = preflight()
    policy_sha = sec.file_sha(PROTOCOL / "bounded-repurchase-ledger-policy-v2.json")
    offline_guard(proof)
    cache = sec.Cache(acq.OUTPUT)
    metadata = json.loads((acq.DISCOVERY / "bounded-source-manifest.json").read_bytes())
    entries = {x["accession"]: x for x in metadata["entries"]}
    filings, rows, components, files = [], [], [], {}
    for number, source in enumerate(inventory["sources"], 1):
        raw = cache.read(source["sha256"])
        entry = entries[source["accession"]]
        acq.check_submission(raw, entry)
        component_rows, filing_rows = [], []
        issues = []
        try:
            documents, header = rules.p.base.resolve_submission(raw, source["accession"])
            unsupported = header["unsupported_container_blocks"]
        except ValueError as error:
            documents, unsupported = [], []
            issues.append(
                {"reason": "CONTAINER_COMPONENT_EXTRACTION_UNRESOLVED", "error_code": str(error)}
            )
        for index, doc in enumerate(documents, 1):
            if raw[doc.start : doc.end] != doc.raw:
                raise ValueError("COMPONENT_RAW_OFFSET_MISMATCH")
            key = rules.p.base.stable(source["accession"], index, doc.start, doc.end, doc.sha256)
            rendered = rules.render(doc)
            text = rendered["text"]
            text_name = "derived-text/" + key + ".txt"
            immutable(OUTPUT / text_name, text.encode())
            files[text_name] = sec.sha(text.encode())
            observations = rules.scan(
                doc, text, entry["form"], str(int(entry["cik_from_historical_index"]))
            )
            for row in observations:
                row.update(
                    accession=source["accession"],
                    component_id=key,
                    filename=doc.filename,
                    raw_doc_form=doc.form,
                    container_sha256=source["sha256"],
                    source_filed=entry["filed"],
                    year=entry["year"],
                    source_role=entry["role"],
                    reporting_name_as_indexed=entry["company_name_as_indexed"],
                    original_source_url=entry["source_url"],
                    derived_text_file=text_name,
                    source_filing_date_is_public_time=False,
                )
                if row["category"] == "CANDIDATE":
                    dated = [
                        row["facts"].get(name)
                        for name in ("approval_date", "stated_announcement_date")
                    ]
                    if any(day and day < entry["filed"] for day in dated):
                        row["unresolved_requirements"].append(
                            "EARLIER_ACTION_DATE_REQUIRES_PRIOR_DISCLOSURE_SEARCH"
                        )
            filing_rows.extend(observations)
            component_rows.append(
                {
                    "component_id": key,
                    "accession": source["accession"],
                    "filename": doc.filename,
                    "raw_doc_form": doc.form,
                    "raw_document_sha256": doc.sha256,
                    "container_byte_start": doc.start,
                    "container_byte_end": doc.end,
                    "container_sha256": source["sha256"],
                    "derived_text_file": text_name,
                    "derived_text_sha256": files[text_name],
                    "extraction_status": rendered["status"],
                    "encoding": rendered["encoding"],
                    "keyword_passages": len(observations),
                    "keyword_occurrences_in_extracted_text": len(rules.KEYWORD.findall(text)),
                    "event_absence_certified": False,
                }
            )
        if len(documents) + len(unsupported) != source["container_validation"]["document_blocks"]:
            issues.append({"reason": "COMPONENT_COUNT_COVERAGE_MISMATCH"})
        filing = {
            "accession": source["accession"],
            "role": source["role"],
            "year": source["year"],
            "container_sha256": source["sha256"],
            "component_count": len(component_rows),
            "unsupported_blocks": unsupported,
            "coverage_issues": issues,
            "non_text_or_unresolved_components": sum(
                x["extraction_status"] != "VISIBLE_TEXT_EXTRACTED" for x in component_rows
            ),
            "keyword_passages": len(filing_rows),
            "candidate_passages": sum(x["category"] == "CANDIDATE" for x in filing_rows),
            "coverage_status": "SOURCE_RETAINED_EXTRACTION_NOT_WHOLE_SOURCE_NEGATIVE",
            "event_absence_certified": False,
        }
        write_json(
            "filings/" + source["accession"] + ".json",
            {"filing": filing, "components": component_rows, "records": filing_rows},
        )
        filings.append(filing)
        rows.extend(filing_rows)
        components.extend(component_rows)
        print(
            json.dumps(
                {
                    "filings_completed": number,
                    "target": 100,
                    "keyword_passages": len(rows),
                    "network_requests": 0,
                }
            ),
            flush=True,
        )
    groups = rules.proposed_groups(rows)
    counts = Counter(x["category"] for x in rows)
    for name, category in (
        ("candidates", "CANDIDATE"),
        ("contexts", "CONTEXT"),
        ("rejections", "REJECTION_CONTEXT"),
        ("unresolved", "UNRESOLVED"),
    ):
        immutable(
            OUTPUT / (name + ".jsonl"),
            b"".join(sec.json_bytes(row) for row in rows if row["category"] == category),
        )
    immutable(OUTPUT / "qualifying-events.jsonl", b"")
    immutable(OUTPUT / "ledger.jsonl", b"".join(sec.json_bytes(row) for row in rows))
    write_json("source-coverage.json", {"filings": filings, "components": components})
    write_json(
        "provisional-action-groups.json",
        {"groups": groups, "distinct_economic_event_count_established": False},
    )
    # The source-only frame is separate from predictions and assigns no reviewer.
    ranked = sorted(
        groups,
        key=lambda x: hashlib.sha256(
            (policy_sha + "|" + x["provisional_group_id"]).encode()
        ).hexdigest(),
    )[:30]
    write_json(
        "prospective-review-source-frame.json",
        {
            "candidate_source_packets": [
                {"packet_id": x["provisional_group_id"], "accession": x["accession"]}
                for x in ranked
            ],
            "source_control_packets": [
                {"accession": x["accession"]}
                for x in inventory["sources"]
                if x["role"] == "SOURCE_CONTROL"
            ],
            "reviewer": None,
            "independent_decisions": 0,
            "review_status": "NOT_STARTED",
            "owner_questionnaire_opened": False,
            "frame_is_not_independent_gold": True,
            "provisional_frame_not_final_cluster_sample": True,
            "final_packet_requires_action_identity_review": True,
            "predictions_must_be_withheld_from_reviewer": True,
        },
    )
    summary = {
        "version": "bounded-repurchase-ledger-v2",
        "status": "PROVISIONAL_SOURCE_FACT_LEDGER_PENDING_INDEPENDENT_QA",
        "input_acquired_manifest_sha256": ACQUIRED_SHA,
        "input_acquisition_audit_sha256": AUDIT_SHA,
        "rules_version": rules.VERSION,
        "policy_sha256": policy_sha,
        "code_registry": proof["registry"],
        "source_filings": len(filings),
        "source_roles": dict(Counter(x["role"] for x in filings)),
        "components": len(components),
        "extraction_status_counts": dict(Counter(x["extraction_status"] for x in components)),
        "source_coverage_issue_filings": sum(
            bool(x["coverage_issues"] or x["unsupported_blocks"]) for x in filings
        ),
        "filings_with_non_text_or_unresolved_components": sum(
            x["non_text_or_unresolved_components"] > 0 for x in filings
        ),
        "keyword_passages": len(rows),
        "category_counts": dict(counts),
        "proposed_state_counts": dict(Counter(x["proposed_state"] for x in rows)),
        "provisional_action_groups": len(groups),
        "distinct_economic_event_count_established": False,
        "candidate_source_filings": len(
            {x["accession"] for x in rows if x["category"] == "CANDIDATE"}
        ),
        "control_filings_with_keyword_passages": sum(
            x["role"] == "SOURCE_CONTROL" and x["keyword_passages"] > 0 for x in filings
        ),
        "qualifying_events": 0,
        "independent_factual_decisions": 0,
        "independent_QA_passed": False,
        "public_times_certified": 0,
        "full_action_dedup_certified": False,
        "original_718_sample_changes": 0,
        "new_filing_downloads": 0,
        "network_requests": 0,
        "full_extraction_authorized": False,
        "final_qa_authorized": False,
        "phase3_authorized": False,
        "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
    }
    write_json("summary.json", summary)
    immutable(OUTPUT / "ledger.html", readable_report(summary, rows).encode())
    for path in OUTPUT.rglob("*"):
        if path.is_file() and path.name not in {"output-manifest.json", "validation.json"}:
            files[str(path.relative_to(OUTPUT)).replace("\\", "/")] = sec.file_sha(path)
    write_json(
        "output-manifest.json",
        {"files": files, "source_sha256": ACQUIRED_SHA, "code_registry": proof["registry"]},
    )
    return summary


def validate() -> dict[str, Any]:
    inventory, proof = preflight()
    offline_guard(proof)
    manifest = json.loads((OUTPUT / "output-manifest.json").read_bytes())
    if manifest["source_sha256"] != ACQUIRED_SHA or manifest["code_registry"] != proof["registry"]:
        raise ValueError("LEDGER_INPUT_OR_CODE_BINDING_MISMATCH")
    for name, expected in manifest["files"].items():
        if not (OUTPUT / name).resolve().is_relative_to(OUTPUT.resolve()):
            raise ValueError("LEDGER_OUTPUT_PATH_OUTSIDE_NAMESPACE")
        if sec.file_sha(OUTPUT / name) != expected:
            raise ValueError("LEDGER_OUTPUT_HASH_MISMATCH")
    coverage = json.loads((OUTPUT / "source-coverage.json").read_bytes())
    sources = {x["accession"]: x for x in inventory["sources"]}
    if len(coverage["filings"]) != 100 or {x["accession"] for x in coverage["filings"]} != set(
        sources
    ):
        raise ValueError("LEDGER_SOURCE_MEMBERSHIP_MISMATCH")
    cache = sec.Cache(acq.OUTPUT)
    component_map = {x["component_id"]: x for x in coverage["components"]}
    for source in inventory["sources"]:
        raw = cache.read(source["sha256"])
        for component in coverage["components"]:
            if component["accession"] == source["accession"] and (
                component["container_sha256"] != source["sha256"]
                or sec.sha(raw[component["container_byte_start"] : component["container_byte_end"]])
                != component["raw_document_sha256"]
            ):
                raise ValueError("LEDGER_RAW_COMPONENT_REPLAY_MISMATCH")
    rows = [json.loads(line) for line in (OUTPUT / "ledger.jsonl").read_bytes().splitlines()]
    text_cache = {}
    for row in rows:
        component = component_map[row["component_id"]]
        if row["component_id"] not in text_cache:
            text_cache[row["component_id"]] = (OUTPUT / component["derived_text_file"]).read_text(
                encoding="utf-8"
            )
        text = text_cache[row["component_id"]]
        verify_evidence(row, component, text)
    result = {
        "status": "PASS_WITH_EXPLICIT_RESEARCH_LIMITATIONS",
        "source_filings": 100,
        "evidence_records_replayed": len(rows),
        "output_manifest_sha256": sec.file_sha(OUTPUT / "output-manifest.json"),
        "qualifying_events": 0,
        "independent_QA_passed": False,
        "network_requests": 0,
        "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
    }
    write_json("validation.json", result)
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("command", choices=["build", "validate"])
    command = cli.parse_args().command
    try:
        result = build() if command == "build" else validate()
        print(json.dumps({k: v for k, v in result.items() if k != "code_registry"}), flush=True)
    except (ValueError, OSError, RuntimeError, KeyError, sec.AcquisitionError) as error:
        reason = (
            str(error)
            if isinstance(error, (ValueError, sec.AcquisitionError))
            else type(error).__name__
        )
        print(
            json.dumps({"status": "LEDGER_STOPPED_NO_RESEARCH_GATE_OPENED", "reason": reason}),
            flush=True,
        )
        raise SystemExit(2) from None
