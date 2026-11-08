"""Isolated bounded parser-development pilot. No full extraction or financial outcomes."""

from __future__ import annotations

import argparse
import html
import importlib.util
import json
import re
import sqlite3
import ssl
import sys
import time
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "protocols/corporate-event-alpha-v1"
PRIVATE = ROOT / "reports/corporate-event-alpha-v1/phase2-private"
PILOT = PRIVATE / "parser-qa-v1"
META_ID = "sec-index-metadata-v1-cached-completion-20261108T181347Z"
META_SHA = "2e00caafed8eb2ff3c5fd4410f1a8c592b6ac622640c1d4f2989783cb4237894"
REGISTERED = (
    "tools/corporate_events/parser_qa.py",
    "tools/corporate_events/event_parsers.py",
    "tools/corporate_events/sec_document_client.py",
    "tests/test_event_parsers.py",
    "tests/test_parser_qa.py",
    "protocols/corporate-event-alpha-v1/parser-qa-policy.json",
)


def load(name: str, relative: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    if spec is None or spec.loader is None:
        raise RuntimeError("REGISTERED_MODULE_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sec = load("cea_sec_census", "tools/corporate_events/sec_census.py")
base = load("cea_frozen_events", "tools/corporate_events/phase2.py")
parsers = load("cea_event_parsers", "tools/corporate_events/event_parsers.py")
documents = load("cea_document_client", "tools/corporate_events/sec_document_client.py")


def preflight() -> dict[str, Any]:
    proof = sec.preflight()
    extension = json.loads((PROTOCOL / "parser-qa-extension.json").read_bytes())
    if set(extension["files"]) != set(REGISTERED):
        raise ValueError("PARSER_EXTENSION_SCOPE_MISMATCH")
    for name, expected in extension["files"].items():
        if sec.file_sha(ROOT / name) != expected:
            raise ValueError("PARSER_EXTENSION_CODE_HASH_MISMATCH")
    manifest_path = PRIVATE / "snapshots" / META_ID / "authority-manifest.json"
    if sec.file_sha(manifest_path) != META_SHA:
        raise ValueError("METADATA_AUTHORITY_IDENTITY_MISMATCH")
    authority = json.loads(manifest_path.read_bytes())
    summary = json.loads((manifest_path.parent / "census-summary.json").read_bytes())
    if authority["status"] != "COMPLETE_METADATA_CENSUS" or summary["completed_indices"] != 76:
        raise ValueError("METADATA_CENSUS_NOT_COMPLETE")
    if authority["protocol"]["manifest_sha256"] != sec.FROZEN_SHA:
        raise ValueError("METADATA_PROTOCOL_BINDING_MISMATCH")
    for name, expected in authority["files"].items():
        if sec.file_sha(manifest_path.parent / name) != expected:
            raise ValueError("METADATA_ARTIFACT_HASH_MISMATCH")
    policy = json.loads((PROTOCOL / "parser-qa-policy.json").read_bytes())
    if (
        policy["full_extraction_authorized"]
        or policy["scope"] != "BOUNDED_DEVELOPMENT_PILOT_NOT_FINAL_QA"
    ):
        raise ValueError("PARSER_PILOT_SCOPE_FAILURE")
    if policy["max_submissions"] != 108 or policy["max_received_body_bytes"] != 536_870_912:
        raise ValueError("USER_APPROVED_PILOT_BOUNDS_MISMATCH")
    return {
        **proof,
        "metadata_snapshot_id": META_ID,
        "metadata_authority_sha256": META_SHA,
        "parser_extension_sha256": sec.file_sha(PROTOCOL / "parser-qa-extension.json"),
        "parser_code_hashes": extension["files"],
        "pilot_scope": policy["scope"],
    }


def install() -> None:
    # Only this entrypoint's separately hash-verified sources extend the trusted code files.
    sec.EXTENSION_FILES = (*sec.EXTENSION_FILES, *REGISTERED, "tools/corporate_events/phase2.py")
    sec.install_firewall()


def era(filed: str) -> str:
    return (
        "2006-2012" if filed[:4] <= "2012" else "2013-2022" if filed[:4] <= "2022" else "2023-2024"
    )


def pilot_rank(era_name: str, form: str, accession: str, filename: str) -> str:
    return str(
        sec.sha(
            "|".join(
                (
                    "corporate-event-alpha-v1",
                    "parser-development-pilot-v1",
                    era_name,
                    form,
                    accession,
                    filename,
                )
            ).encode()
        )
    )


def plan_pilot(proof: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    if (PILOT / "plan.json").exists():
        raise ValueError("IMMUTABLE_PILOT_PLAN_ALREADY_EXISTS")
    best: dict[tuple[str, str], dict[str, Any]] = {}
    population: Counter[tuple[str, str]] = Counter()
    meta = PRIVATE / "snapshots" / META_ID
    source = [
        json.loads(line) for line in (meta / "source-objects.jsonl").read_bytes().splitlines()
    ]
    metadata_cache = sec.Cache(PRIVATE)
    observed = 0
    for record in source:
        raw = metadata_cache.read(record["sha256"])
        content, encoding = sec.index_payload(raw, record["url"])
        header = False
        for line in content.decode(encoding.removesuffix("-lossless")).split("\n"):
            line = line.rstrip("\r")
            if not header:
                header = line == "CIK|Company Name|Form Type|Date Filed|Filename"
                continue
            if not line.strip() or set(line.strip()) == {"-"}:
                continue
            fields = [field.strip() for field in line.split("|")]
            if len(fields) != 5:
                raise ValueError("VERIFIED_INDEX_STRUCTURE_CHANGED")
            observed += 1
            if fields[2] not in aliases or not "2006-01-01" <= fields[3] <= "2024-12-31":
                continue
            cell = era(fields[3]), fields[2]
            population[cell] += 1
            accession = fields[4].rsplit("/", 1)[-1].removesuffix(".txt")
            rank = pilot_rank(*cell, accession, fields[4])
            if cell not in best or (rank, fields[4]) < (
                best[cell]["pilot_rank_sha256"],
                best[cell]["index_filename"],
            ):
                best[cell] = {
                    "era": cell[0],
                    "form": fields[2],
                    "canonical_form": aliases[fields[2]],
                    "cik_raw": fields[0],
                    "filing_date": fields[3],
                    "index_filename": fields[4],
                    "accession": accession,
                    "url": "https://www.sec.gov/Archives/" + fields[4],
                    "pilot_rank_sha256": rank,
                    "metadata_record_sha256": sec.IndexRow(*fields).record_hash().hex(),
                    "index_object_sha256": record["sha256"],
                    "selected_before_candidate_filter": True,
                }
        print(
            json.dumps(
                {
                    "metadata_index_scanned": f"{record['year']}Q{record['quarter']}",
                    "pilot_cells": len(best),
                }
            ),
            flush=True,
        )
    if observed != 19_689_306 or len(best) > 108:
        raise ValueError("PILOT_FRAME_RECONCILIATION_FAILURE")
    entries = [best[key] for key in sorted(best)]
    allowed = {entry["url"] for entry in entries} | {documents.CATALOGUE}
    for url in allowed:
        documents.validate_url(url, allowed)
    plan = {
        "scope": "BOUNDED_DEVELOPMENT_PILOT_NOT_FINAL_QA",
        "protocol": proof,
        "created_at_utc": sec.utc_now().isoformat(),
        "entries": entries,
        "max_submissions": 108,
        "max_received_body_bytes": 536_870_912,
        "metadata_source_rows_scanned": observed,
        "metadata_cell_populations": [
            {"era": key[0], "form": key[1], "records": count}
            for key, count in sorted(population.items())
        ],
        "pilot_rank_domain": (
            "corporate-event-alpha-v1|parser-development-pilot-v1|era|form|accession|index_filename"
        ),
        "pilot_rank_is_not_frozen_qa_rank": True,
        "no_failed_or_uninformative_replacements": True,
        "technical_resources": [documents.CATALOGUE],
        "full_extraction_authorized": False,
    }
    sec.write_new(PILOT / "plan.json", sec.json_bytes(plan))
    sec.write_new(
        PILOT / "plan-binding.json",
        sec.json_bytes(
            {
                "plan_sha256": sec.file_sha(PILOT / "plan.json"),
                "policy_sha256": sec.file_sha(PROTOCOL / "parser-qa-policy.json"),
                "metadata_authority_sha256": META_SHA,
                "selection_code_commit": proof["code_commit"],
            }
        ),
    )
    return {
        "status": "PILOT_PLAN_FROZEN",
        "selected_cells": len(entries),
        "unique_submission_urls": len(allowed) - 1,
        "plan_sha256": sec.file_sha(PILOT / "plan.json"),
    }


def read_plan() -> tuple[dict[str, Any], str]:
    raw = (PILOT / "plan.json").read_bytes()
    plan = json.loads(raw)
    binding = json.loads((PILOT / "plan-binding.json").read_bytes())
    if (
        binding["plan_sha256"] != sec.sha(raw)
        or binding["policy_sha256"] != sec.file_sha(PROTOCOL / "parser-qa-policy.json")
        or binding["metadata_authority_sha256"] != META_SHA
    ):
        raise ValueError("PILOT_PLAN_BINDING_MISMATCH")
    if (
        plan["scope"] != "BOUNDED_DEVELOPMENT_PILOT_NOT_FINAL_QA"
        or plan["protocol"]["metadata_authority_sha256"] != META_SHA
    ):
        raise ValueError("PILOT_PLAN_IDENTITY_MISMATCH")
    if len(plan["entries"]) > 108 or plan["max_received_body_bytes"] != 536_870_912:
        raise ValueError("PILOT_PLAN_BOUNDS_MISMATCH")
    return plan, sec.sha(raw)


def acquire(
    run: Path,
    plan: dict[str, Any],
    plan_sha: str,
    agent: str,
    context: ssl.SSLContext,
    proof: dict[str, Any],
) -> dict[str, Any]:
    urls = sorted({entry["url"] for entry in plan["entries"]})
    allowed = set(urls) | {documents.CATALOGUE}
    results = []
    blocker = None
    with sec.Scheduler(PRIVATE) as scheduler:
        budget = documents.ByteBudget(PILOT / "download-budget.json", 536_870_912, plan_sha)
        transport = documents.HttpsDocuments(sec, allowed, context, budget)
        client = documents.DocumentClient(
            sec, PILOT, run, scheduler, transport, allowed, agent, time.time() + 1800
        )
        for url in [documents.CATALOGUE, *urls]:
            try:
                raw, record = client.fetch(url)
                results.append({"url": url, "status": "CACHED", **record})
                print(json.dumps({"source_cached": url, "bytes": len(raw)}), flush=True)
            except (sec.AcquisitionError, ValueError, OSError) as error:
                reason = (
                    str(error)
                    if isinstance(error, (sec.AcquisitionError, ValueError))
                    else type(error).__name__
                )
                results.append({"url": url, "status": "UNAVAILABLE_RETAINED", "reason": reason})
                client.fail(url, reason, 0)
                if (
                    "403" in reason
                    or "429" in reason
                    or "BUDGET" in reason
                    or "DEFERRED" in reason
                    or "FIREWALL" in reason
                    or "BLOCKED" in reason
                    or "HASH_MISMATCH" in reason
                    or "BUDGET_IDENTITY" in reason
                ):
                    blocker = reason
                    break
        result = {
            "kind": "BOUNDED_PARSER_DEVELOPMENT_ACQUISITION",
            "protocol": proof,
            "plan_sha256": plan_sha,
            "objects": results,
            "blocker": blocker,
            "received_body_bytes_total": budget.spent,
            "download_byte_budget": budget.limit,
            "planned_submission_urls": len(urls),
            "full_extraction_authorized": False,
            "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
        }
        sec.write_new(run / "acquisition-manifest.json", sec.json_bytes(result))
    return {
        "status": "BOUNDED_ACQUISITION_COMPLETE"
        if blocker is None
        else "BOUNDED_ACQUISITION_STOPPED",
        "blocker": blocker,
        "cached": sum(row["status"] == "CACHED" for row in results),
        "run_id": run.name,
    }


def source_families(canonical: str) -> tuple[str, ...]:
    if canonical in {"4", "4/A"}:
        return ("INSIDER_OPEN_MARKET_PURCHASE",)
    if canonical in {"INITIAL_13D", "AMENDED_13D", "PASSIVE_13G", "AMENDED_13G"}:
        return ("ACTIVIST_13D",)
    if canonical in {"8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A"}:
        return ("SHARE_REPURCHASE", "DIVIDEND_POSITIVE_CHANGE", "SPINOFF_ANNOUNCEMENT")
    return ("SPINOFF_ANNOUNCEMENT",)


def persist_document(
    db: sqlite3.Connection,
    doc: Any,
    entry: dict[str, Any],
    parsed: dict[str, Any],
    header: dict[str, Any],
    record: dict[str, Any],
    observed: str,
) -> tuple[list[dict[str, Any]], str]:
    doc_id = doc.source_id
    observations = parsed.get("observations", [])
    issuer_raw = next(
        (
            (row["facts"].get("subject_issuer_cik") or row["facts"].get("issuer_cik_raw"))
            for row in observations
            if (row["facts"].get("subject_issuer_cik") or row["facts"].get("issuer_cik_raw"))
        ),
        None,
    )
    if not issuer_raw and entry["canonical_form"] not in {
        "4",
        "4/A",
        "INITIAL_13D",
        "AMENDED_13D",
        "PASSIVE_13G",
        "AMENDED_13G",
    }:
        issuer_raw = entry["cik_raw"]
    normalized = base.canonical_cik(issuer_raw)
    issuer_id = (
        parsers.stable("issuer", normalized)
        if normalized
        else parsers.stable("unresolved-issuer", doc_id)
    )
    db.execute(
        "INSERT OR IGNORE INTO issuer_identities VALUES (?,?,?,?,?)",
        (
            issuer_id,
            issuer_raw,
            normalized,
            "RESOLVED_SEC" if normalized else "UNRESOLVED",
            "SEC_INDEX_OR_XML_ROLE; no vendor security mapping",
        ),
    )
    db.execute(
        "INSERT INTO disclosures VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            doc_id,
            issuer_id,
            doc.accession,
            entry["form"],
            entry["canonical_form"],
            doc.filename,
            doc.sha256,
            parsed.get("schema_version"),
            doc.kind,
            entry["filing_date"],
            observed,
        ),
    )
    source_evidence = parsers.stable("source", doc_id)
    db.execute(
        "INSERT INTO source_evidence VALUES (?,?,?,?,?,?,?,?)",
        (
            source_evidence,
            doc_id,
            record["sha256"],
            record["object_locator"],
            f"container-bytes:{doc.start}:{doc.end};document-sha256:{doc.sha256}",
            "ORIGINAL_SEC_DOCUMENT",
            None,
            "Immutable raw source; historical public cutoff unresolved",
        ),
    )
    timing_id = parsers.stable("timing", doc_id)
    header_evidence = parsers.stable("header-evidence", doc_id)
    db.execute(
        "INSERT INTO source_evidence VALUES (?,?,?,?,?,?,?,?)",
        (
            header_evidence,
            doc_id,
            record["sha256"],
            record["object_locator"],
            f"container-header-bytes:0:{header.get('header_byte_end', 0)}",
            "RAW_ACCEPTANCE_ONLY_NOT_PUBLIC_PROOF",
            None,
            "Raw SGML acceptance; source timezone semantics and dissemination unresolved",
        ),
    )
    db.execute(
        (
            "INSERT INTO timing_authority (timing_id,disclosu"
            "re_id,evidence_id,filing_date,acceptance_raw,pub"
            "lic_time_basis,timestamp_precision,observed_at_u"
            "tc) VALUES (?,?,?,?,?,?,?,?)"
        ),
        (
            timing_id,
            doc_id,
            header_evidence,
            entry["filing_date"],
            header.get("acceptance_raw"),
            "TIMESTAMP_UNRESOLVED",
            "UNRESOLVED",
            observed,
        ),
    )
    candidates = []
    for observation in observations:
        if not observation["parser_candidate"]:
            continue
        family, facts, proof = observation["family"], observation["facts"], observation["evidence"]
        event_id = parsers.stable(
            "corporate-event-alpha-v1", family, doc_id, observation["economic_action_anchor"]
        )
        # Explicit columns keep the frozen ten-column event schema exact.
        db.execute(
            (
                "INSERT INTO events (event_id,family,issuer_id,fi"
                "rst_disclosure_id,economic_action_anchor,timing_"
                "id,public_disclosure_date,split_label,dispositio"
                "n,created_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?)"
            ),
            (
                event_id,
                family,
                issuer_id,
                doc_id,
                observation["economic_action_anchor"],
                timing_id,
                None,
                "UNRESOLVED_DATE",
                "CANDIDATE",
                observed,
            ),
        )
        evidence_id = proof["evidence_id"]
        db.execute(
            "INSERT OR IGNORE INTO source_evidence VALUES (?,?,?,?,?,?,?,?)",
            (
                evidence_id,
                doc_id,
                record["sha256"],
                record["object_locator"],
                proof["locator"],
                "PARSER_FACT_PROPOSAL",
                None,
                "Evidence hash/locator in parser output; proposal requires blinded review",
            ),
        )
        db.execute(
            (
                "INSERT INTO event_revisions (revision_id,event_i"
                "d,supersedes_revision_id,disclosure_id,evidence_"
                "id,subtype,disposition,classification_available_"
                "at,recorded_at_utc,reason) VALUES (?,?,?,?,?,?,?"
                ",?,?,?)"
            ),
            (
                parsers.stable("revision", event_id, parsers.VERSION),
                event_id,
                None,
                doc_id,
                evidence_id,
                observation["proposed_state"],
                "CANDIDATE",
                None,
                observed,
                "PARSER_PROPOSAL_PENDING_REVIEW",
            ),
        )
        db.execute(
            "INSERT INTO event_security_mapping_placeholders VALUES (?,?,?,?,?)",
            (
                event_id,
                facts.get("security_title") or facts.get("sec_class_label"),
                None,
                None,
                "SECURITY_MAPPING_UNRESOLVED",
            ),
        )
        for code in ["SECURITY_MAPPING_UNRESOLVED", "TIMESTAMP_UNRESOLVED"]:
            db.execute(
                "INSERT INTO mapping_coverage_status VALUES (?,?,?,?,?,?,?)",
                (
                    parsers.stable(event_id, code),
                    event_id,
                    code,
                    "PHASE2_SEC_ONLY",
                    "Not assessed from market data; no public dissemination proof inferred",
                    evidence_id,
                    observed,
                ),
            )
        state = observation["proposed_state"]
        if family == "INSIDER_OPEN_MARKET_PURCHASE":
            db.execute(
                "INSERT INTO insider_purchase_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    proof["locator"],
                    facts["table_identifier"],
                    facts["transaction_code"],
                    facts["acquired_disposed"],
                    facts["shares_decimal"],
                    facts["ownership_form"],
                    facts["transaction_date"],
                    facts["plan_adoption_date"],
                    facts["venue_state"],
                    facts["discretion_state"],
                    facts["instrument_state"],
                    evidence_id,
                ),
            )
            grouped_owners: dict[str, list[dict[str, Any]]] = {}
            for owner in facts["reporting_owners"]:
                owner_id = parsers.stable("owner", owner["cik_raw"], owner["name"])
                grouped_owners.setdefault(owner_id, []).append(owner)
            for owner_id, statements in grouped_owners.items():
                owner = statements[0]
                db.execute(
                    "INSERT OR IGNORE INTO reporting_owners VALUES (?,?,?,?)",
                    (
                        owner_id,
                        owner["cik_raw"],
                        base.canonical_cik(owner["cik_raw"]),
                        owner["name"] or "",
                    ),
                )
                relation = json.dumps(
                    [
                        {
                            key: claim[key]
                            for key in (
                                "director",
                                "officer",
                                "ten_percent_owner",
                                "other",
                                "officer_title",
                            )
                        }
                        for claim in statements
                    ],
                    sort_keys=True,
                )
                titles = {claim["officer_title"] for claim in statements if claim["officer_title"]}
                title = next(iter(titles)) if len(titles) == 1 else None
                db.execute(
                    "INSERT INTO event_reporting_owners VALUES (?,?,?,?,?)",
                    (event_id, owner_id, observation["economic_action_anchor"], relation, title),
                )
        elif family == "ACTIVIST_13D":
            db.execute(
                "INSERT INTO activist_facts VALUES (?,?,?,?,?,?,?)",
                (event_id, state, 1, evidence_id, None, None, None),
            )
        elif family == "SHARE_REPURCHASE":
            db.execute(
                (
                    "INSERT INTO repurchase_facts (event_id,state,cap"
                    "acity_increase_evidence_id) VALUES (?,?,?)"
                ),
                (event_id, state, evidence_id if state == "AUTHORIZATION_INCREASE" else None),
            )
        elif family == "DIVIDEND_POSITIVE_CHANGE":
            db.execute(
                (
                    "INSERT INTO dividend_facts (event_id,state,decla"
                    "red_cash_per_share_decimal,currency,sec_class_la"
                    "bel,frequency) VALUES (?,?,?,?,?,?)"
                ),
                (
                    event_id,
                    state,
                    facts["declared_cash_per_share_decimal"],
                    facts["currency"],
                    facts["sec_class_label"],
                    facts["frequency"],
                ),
            )
        elif family == "SPINOFF_ANNOUNCEMENT":
            db.execute(
                (
                    "INSERT INTO spinoff_facts (event_id,state,parent_cik_raw,"
                    "structure_from_sec,intent_evidence_id,earlier_disclosure_search_status) "
                    "VALUES (?,?,?,?,?,?)"
                ),
                (
                    event_id,
                    state,
                    entry["cik_raw"],
                    proof["locator"],
                    evidence_id,
                    facts["first_disclosure_status"],
                ),
            )
        candidates.append({**observation, "event_id": event_id, "disclosure_id": doc_id})
    return candidates, doc_id


def parse_pilot(
    run: Path, plan: dict[str, Any], plan_sha: str, proof: dict[str, Any]
) -> dict[str, Any]:
    sec.write_new(run / "preflight.json", sec.json_bytes(proof))
    sec.write_new(run / "observations.jsonl", b"")
    sec.write_new(run / "sources.jsonl", b"")
    schema = (PROTOCOL / "event-schema.sql").read_text("utf-8")
    base.validate_schema(schema)
    cache = sec.Cache(PILOT)
    source_results, qa_rows, proposals = [], [], []
    retained_states: Counter[tuple[str, str]] = Counter()
    seen_docs: set[str] = set()
    observed = sec.utc_now().isoformat()
    database_path = run / "development-events.sqlite3"
    with sqlite3.connect(database_path) as db:
        db.executescript(schema)
        db.set_authorizer(sec.sqlite_authorizer)
        db.execute("INSERT INTO protocol_binding VALUES (?,?)", ("phase1d", sec.FROZEN_SHA))
        for entry in plan["entries"]:
            record = cache.latest(entry["url"])
            if not record:
                source_results.append(
                    {
                        "url": entry["url"],
                        "status": "NOT_ACQUIRED_RETAINED",
                        "form": entry["form"],
                        "era": entry["era"],
                    }
                )
                continue
            raw = cache.read(record["sha256"])
            documents.validate_payload(raw, entry["url"])
            try:
                resolved, header = parsers.resolve_submission(raw, entry["accession"])
            except ValueError as error:
                source_results.append(
                    {
                        "url": entry["url"],
                        "status": "CONTAINER_UNRESOLVED_RETAINED",
                        "reason": str(error),
                        "sha256": record["sha256"],
                    }
                )
                continue
            source_results.append(
                {
                    "url": entry["url"],
                    "status": header["container_status"],
                    "documents": len(resolved),
                    "unsupported_container_blocks": header.get("unsupported_container_blocks", []),
                    "sha256": record["sha256"],
                }
            )
            preview_words_remaining = 80
            for doc in resolved:
                if doc.source_id in seen_docs:
                    continue
                seen_docs.add(doc.source_id)
                binary = (
                    doc.filename.lower().endswith(
                        (".pdf", ".jpg", ".jpeg", ".gif", ".png", ".zip", ".xls", ".xlsx")
                    )
                    or doc.form.upper().startswith("EX-101")
                    or doc.raw.lstrip().startswith((b"<PDF>", b"%PDF", b"begin "))
                )
                if binary:
                    parsed: dict[str, Any] = {
                        "status": "UNSUPPORTED_COMPONENT_RETAINED",
                        "observations": [],
                    }
                elif entry["canonical_form"] in {"4", "4/A"} and doc.kind == "OWNERSHIP_XML":
                    parsed = parsers.ownership(doc)
                else:
                    parsed = parsers.narrative(doc, entry["canonical_form"], entry["cik_raw"])
                for observation in parsed.get("observations", []):
                    if observation["family"] == "ACTIVIST_13D":
                        observation["facts"]["subject_issuer_cik"] = header.get("subject_cik_raw")
                        observation["facts"]["reporting_person_header_cik"] = header.get(
                            "filer_cik_raw"
                        )
                        observation["facts"]["reporting_person_header_name"] = header.get(
                            "filer_name"
                        )
                        if header.get("subject_cik_raw"):
                            observation["facts"]["role_resolution"] = (
                                "EXPLICIT_SGML_SUBJECT_ROLE; OTHER_REPORTERS_REQUIRE_REVIEW"
                            )
                candidate_rows, disclosure_id = persist_document(
                    db, doc, entry, parsed, header, record, observed
                )
                proposals.extend(candidate_rows)
                document_record = {
                    "source_id": doc.source_id,
                    "source_evidence_id": parsers.stable("source", doc.source_id),
                    "accession": doc.accession,
                    "filename": doc.filename,
                    "source_document_sha256": doc.sha256,
                    "container_sha256": record["sha256"],
                    "container_locator": record["object_locator"],
                    "container_byte_start": doc.start,
                    "container_byte_end": doc.end,
                    "form": entry["form"],
                    "source_kind": doc.kind,
                    "era": entry["era"],
                    "parse_status": parsed["status"],
                    "schema_version": parsed.get("schema_version"),
                    "schema_validation": parsed.get("schema_validation"),
                    "public_time_status": "TIMESTAMP_UNRESOLVED",
                    "classification_available_at": None,
                    "split_label": "UNRESOLVED_DATE",
                    "observation_count": len(parsed.get("observations", [])),
                    "derived_text_sha256": parsed.get("derived_text_sha256"),
                    "raw_doc_form": doc.form,
                    "selected_before_candidate_filter": True,
                }
                sec.append_record(run / "sources.jsonl", document_record)
                for row in parsed.get("observations", []):
                    retained_states[row["family"], row["proposed_state"]] += 1
                    sec.append_record(
                        run / "observations.jsonl", {"source_id": doc.source_id, **row}
                    )
                for family in source_families(entry["canonical_form"]):
                    roles = ["INDEPENDENT_SOURCE_CONTROL"]
                    if any(
                        row["family"] == family and row["parser_candidate"]
                        for row in parsed.get("observations", [])
                    ):
                        roles.append("PARSER_CANDIDATE")
                    for role in roles:
                        row = {**document_record, "family": family, "role": role}
                        qa_rows.append(row)
                        rank = parsers.qa_rank(
                            family, entry["era"], entry["form"], doc.kind, doc.accession, doc.sha256
                        )
                        db.execute(
                            "INSERT INTO qa_source_controls VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (
                                parsers.stable("qa-control", disclosure_id, family, role),
                                disclosure_id,
                                family,
                                entry["era"],
                                role,
                                1,
                                rank,
                                doc.source_id,
                                observed,
                                "Development frame selected before parser filtering; not final QA",
                            ),
                        )
                preview = parsed.get("text")
                if binary:
                    preview = (
                        "Unsupported component retained in original conta"
                        "iner; no content inference."
                    )
                elif preview is None:
                    preview, _ = parsers.text_document(doc.raw)
                # Only a short preview is reproduced; full original bytes stay in the private cache.
                preview_words = preview.split()[:preview_words_remaining]
                snippet = " ".join(preview_words)
                preview_words_remaining -= len(preview_words)
                packet = {
                    **document_record,
                    "packet_scope": "DOCUMENT_FACT_REVIEW_ONLY_TIMING_UNRESOLVED",
                    "public_cutoff_utc": None,
                    "later_documents_included": False,
                    "candidate_proposals": candidate_rows,
                    "preview_word_limit_per_container": 80,
                    "schema_certification": "PENDING_OFFICIAL_HISTORICAL_SCHEMA_REVIEW",
                    "review_status": "PENDING",
                    "market_fields_included": False,
                }
                component_path = run / "documents" / (doc.sha256 + ".txt")
                if component_path.exists():
                    if sec.file_sha(component_path) != doc.sha256:
                        raise ValueError("IMMUTABLE_COMPONENT_HASH_MISMATCH")
                else:
                    sec.write_new(component_path, doc.raw)
                packet["original_component_file"] = "documents/" + doc.sha256 + ".txt"
                sec.write_new(run / "packets" / (doc.source_id + ".json"), sec.json_bytes(packet))
                page = (
                    (
                        "<!doctype html><html><head><meta charset='utf-8'"
                        "><meta http-equiv='Content-Security-Policy' cont"
                        "ent=\"default-src 'none'; style-src 'unsafe-inlin"
                        "e'\"><title>Blinded SEC development review</title"
                        "><style>body{max-width:1000px;margin:3rem auto;f"
                        "ont:16px system-ui}pre{white-space:pre-wrap}</st"
                        "yle></head><body>"
                    )
                    + (
                        "<h1>Blinded SEC development review</h1><p>Histor"
                        "ical public cutoff unresolved. Proposals require"
                        " review; this packet cannot certify historical a"
                        "vailability.</p>"
                    )
                    + "<p>Source: "
                    + html.escape(doc.filename)
                    + " - "
                    + html.escape(entry["form"])
                    + "</p><p>Document SHA256: "
                    + doc.sha256
                    + "</p><h2>Source preview</h2><pre>"
                    + html.escape(snippet)
                    + "</pre><h2>Fact proposals and evidence locators</h2><pre>"
                    + html.escape(json.dumps(candidate_rows, indent=2))
                    + "</pre><p><a href='../documents/"
                    + doc.sha256
                    + ".txt'>Open original component bytes</a></p><p>Original source bytes: "
                    + html.escape(record["object_locator"])
                    + "; container range "
                    + str(doc.start)
                    + ":"
                    + str(doc.end)
                    + "</p></body></html>"
                )
                sec.write_new(run / "packets" / (doc.source_id + ".html"), page.encode())
                control_page = (
                    "<!doctype html><meta charset='utf-8'><meta http-"
                    "equiv='Content-Security-Policy' content=\"default"
                    "-src 'none'\">"
                    "<h1>Independent source control</h1><p>Review the"
                    " original source before viewing parser proposals"
                    ". Historical public time remains unresolved.</p>"
                    "<p>Source SHA256: "
                    + doc.sha256
                    + "</p><pre>"
                    + html.escape(snippet)
                    + "</pre><p><a href='../documents/"
                    + doc.sha256
                    + ".txt'>Open original component bytes</a></p>"
                )
                sec.write_new(
                    run / "packets" / (doc.source_id + ".control.html"), control_page.encode()
                )
        if (
            db.execute("SELECT count(*) FROM events WHERE disposition='QUALIFYING'").fetchone()[0]
            != 0
        ):
            raise ValueError("AUTOMATIC_QUALIFICATION_FORBIDDEN")
    db.close()
    frozen_sample = PILOT / "frozen-development-qa-selection.json"
    if frozen_sample.exists():
        qa = json.loads(frozen_sample.read_bytes())
        if qa["plan_sha256"] != plan_sha:
            raise ValueError("FROZEN_DEVELOPMENT_QA_PLAN_MISMATCH")
        if any(row["source_id"] not in seen_docs for row in qa["selected"]):
            raise ValueError("FROZEN_DEVELOPMENT_QA_SOURCE_MISSING_NO_REDRAW")
    else:
        qa = parsers.select_qa(qa_rows, frame_scope="BOUNDED_DEVELOPMENT_PILOT_NOT_FINAL_QA")
        qa["plan_sha256"] = plan_sha
        sec.write_new(frozen_sample, sec.json_bytes(qa))
    sec.write_new(run / "development-qa-selection.json", sec.json_bytes(qa))
    table_rows = []
    for case in qa["selected"]:
        suffix = ".control.html" if case["role"] == "INDEPENDENT_SOURCE_CONTROL" else ".html"
        label = " | ".join(
            str(case[key]) for key in ("family", "era", "form", "source_kind", "role")
        )
        table_rows.append(
            "<li><a href='packets/"
            + case["source_id"]
            + suffix
            + "'>"
            + html.escape(label)
            + "</a></li>"
        )
    index_html = (
        "<!doctype html><meta charset='utf-8'><meta http-"
        "equiv='Content-Security-Policy' content=\"default"
        "-src 'none'\">"
        "<h1>Bounded SEC development review</h1><p>This i"
        "s not final QA. No human metrics or historical p"
        "ublic time is certified. Full extraction remains"
        " closed.</p>"
        "<p>Controls open a source-only view. Candidate v"
        "iews include machine proposals. Record decisions"
        " with an actual reviewer ID and source evidence."
        "</p><ul>" + "".join(table_rows) + "</ul>"
    )
    sec.write_new(run / "review-index.html", index_html.encode())
    sec.write_new(run / "acquisition-coverage.json", sec.json_bytes(source_results))
    fields = (
        "event_id",
        "source_id",
        "reviewer_id",
        "decision",
        "reason",
        "evidence_ids",
        "supersedes_review_id",
        "family",
        "role",
        "correct_state",
        "event_presence",
    )
    template: dict[str, Any] = {key: None for key in fields}
    template["scope"] = "DEVELOPMENT_FACT_REVIEW_ONLY"
    sec.write_new(run / "review-input-template.json", sec.json_bytes(template))
    counts = Counter(row["family"] for row in proposals)
    states = Counter((row["family"], row["proposed_state"]) for row in proposals)
    result = {
        "kind": "BOUNDED_PARSER_DEVELOPMENT_REPORT",
        "protocol": proof,
        "plan_sha256": plan_sha,
        "run_id": run.name,
        "source_results": source_results,
        "documents": len(seen_docs),
        "parser_candidates": len(proposals),
        "retained_observations": sum(retained_states.values()),
        "non_candidate_context_observations": sum(retained_states.values()) - len(proposals),
        "all_observation_states": [
            {"family": key[0], "state": key[1], "count": count}
            for key, count in sorted(retained_states.items())
        ],
        "candidates_by_family": dict(counts),
        "proposal_states": [
            {"family": key[0], "state": key[1], "count": count}
            for key, count in sorted(states.items())
        ],
        "qualifying_events_certified": 0,
        "event_database_frozen": False,
        "manual_precision": "NOT_MEASURED",
        "classification_agreement": "NOT_MEASURED",
        "dedup_agreement": "NOT_MEASURED",
        "recall": "NOT_MEASURED",
        "reviewers_supplied": 0,
        "historical_public_times_inferred": 0,
        "full_extraction_gate": "CLOSED_FINAL_QA_FRAME_SCHEMA_AUTHORITY_AND_MANUAL_REVIEW_PENDING",
        "qa_selected_records": len(qa["selected"]),
        "qa_cells": len(qa["cells"]),
        "parser_version": parsers.VERSION,
        "frozen_development_qa_sha256": sec.file_sha(frozen_sample),
        "action_link_status": "UNRESOLVED_SEPARATE_CANDIDATES_NO_SIMILARITY_MERGES",
        "official_schema_catalogue_cached": cache.latest(documents.CATALOGUE) is not None,
        "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
    }
    sec.write_new(run / "parser-development-report.json", sec.json_bytes(result))
    return {
        "status": "DEVELOPMENT_PILOT_PARSED_REVIEW_PENDING",
        "run_id": run.name,
        "documents": len(seen_docs),
        "parser_candidates": len(proposals),
        "full_extraction_gate": result["full_extraction_gate"],
    }


def record_review(run_id: str, input_name: str) -> dict[str, Any]:
    run = PILOT / "runs" / run_id
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}\.json", input_name):
        raise ValueError("REVIEW_INPUT_NAME_INVALID")
    item = json.loads((run / "review-inbox" / input_name).read_bytes())
    allowed = {
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
    if set(item) != allowed or item["scope"] != "DEVELOPMENT_FACT_REVIEW_ONLY":
        raise ValueError("REVIEW_SCHEMA_OR_SCOPE_INVALID")
    if item["decision"] not in {"ACCEPTED", "REJECTED", "AMBIGUOUS"}:
        raise ValueError("REVIEW_DECISION_INVALID")
    if (
        not isinstance(item["reviewer_id"], str)
        or not item["reviewer_id"].strip()
        or not item["reason"]
    ):
        raise ValueError("ACTUAL_REVIEWER_AND_REASON_REQUIRED")
    source_id = item["source_id"]
    if not isinstance(source_id, str) or not re.fullmatch(r"[0-9a-f]{64}", source_id):
        raise ValueError("REVIEW_SOURCE_INVALID")
    packet_path = run / "packets" / (source_id + ".json")
    packet = json.loads(packet_path.read_bytes())
    proposals = packet["candidate_proposals"]
    if item["family"] not in parsers.FAMILIES or item["role"] not in {
        "PARSER_CANDIDATE",
        "INDEPENDENT_SOURCE_CONTROL",
    }:
        raise ValueError("REVIEW_FAMILY_OR_ROLE_INVALID")
    selected = json.loads((PILOT / "frozen-development-qa-selection.json").read_bytes())["selected"]
    if not any(
        row["source_id"] == source_id
        and row["family"] == item["family"]
        and row["role"] == item["role"]
        for row in selected
    ):
        raise ValueError("REVIEW_CASE_NOT_IN_FIXED_DEVELOPMENT_SAMPLE")
    protocol = tomllib.loads((PROTOCOL / "event-data-protocol.toml").read_text("utf-8"))
    labels = {
        "SHARE_REPURCHASE": set(
            protocol["repurchases"]["eligible_classes"] + protocol["repurchases"]["context_classes"]
        ),
        "INSIDER_OPEN_MARKET_PURCHASE": set(
            protocol["form4"]["venue_states"]
            + protocol["form4"]["discretion_states"]
            + protocol["form4"]["instrument_states"]
            + ["AMENDMENT_CORRECTION", "REJECTED_NONQUALIFYING"]
        ),
        "ACTIVIST_13D": set(protocol["activist13d"]["intent_states"]),
        "DIVIDEND_POSITIVE_CHANGE": set(
            protocol["dividends"]["positive_subtypes"] + protocol["dividends"]["context_states"]
        ),
        "SPINOFF_ANNOUNCEMENT": set(protocol["spinoffs"]["states"]),
    }
    if item["correct_state"] not in labels[item["family"]] | {
        "NO_EVENT",
        "UNRESOLVED",
        "EVENT_PRESENT_MULTIPLE_STATES",
    }:
        raise ValueError("REVIEW_DOCUMENTARY_STATE_LABEL_REQUIRED")
    if item["event_presence"] is not None and not isinstance(item["event_presence"], bool):
        raise ValueError("REVIEW_PRESENCE_LABEL_INVALID")
    if item["role"] == "PARSER_CANDIDATE" and not any(
        row["event_id"] == item["event_id"] and row["family"] == item["family"] for row in proposals
    ):
        raise ValueError("CANDIDATE_REVIEW_EVENT_AND_FAMILY_REQUIRED")
    if item["role"] == "INDEPENDENT_SOURCE_CONTROL" and item["event_id"] is not None:
        raise ValueError("SOURCE_CONTROL_IS_NOT_A_PARSER_EVENT_LABEL")
    if item["event_id"] not in {row["event_id"] for row in proposals} | {None}:
        raise ValueError("REVIEW_EVENT_NOT_IN_PACKET")
    known = {row["evidence"]["evidence_id"] for row in proposals} | {packet["source_evidence_id"]}
    if not isinstance(item["evidence_ids"], list) or not set(item["evidence_ids"]).issubset(known):
        raise ValueError("REVIEW_EVIDENCE_NOT_IN_PACKET")
    history = run / "fact-review-history.jsonl"
    old = (
        [json.loads(line) for line in history.read_bytes().splitlines()] if history.exists() else []
    )
    supersedes = item["supersedes_review_id"]
    if supersedes and not any(
        row["review_id"] == supersedes
        and row["reviewer_id"] == item["reviewer_id"]
        and row["source_id"] == source_id
        and row["family"] == item["family"]
        and row["role"] == item["role"]
        for row in old
    ):
        raise ValueError("REVIEW_SUPERSESSION_INVALID")
    record = {
        **item,
        "reviewed_at_utc": sec.utc_now().isoformat(),
        "packet_sha256": sec.file_sha(packet_path),
        "input_sha256": sec.file_sha(run / "review-inbox" / input_name),
        "previous_record_sha256": old[-1]["review_id"] if old else None,
        "classification_available_at": None,
        "historical_time_certified": False,
        "full_extraction_gate": "CLOSED_DEVELOPMENT_REVIEW_ONLY",
    }
    record["review_id"] = parsers.stable("review", record)
    sec.append_record(history, record)
    return {
        "status": "FACT_REVIEW_APPENDED",
        "review_id": record["review_id"],
        "formal_historical_classification_review": False,
        "reason": (
            "Packet public cutoff unresolved; no formal froze"
            "n review row or historical time is fabricated"
        ),
    }


def finalize_run(run: Path, result: dict[str, Any]) -> None:
    files = {}
    for path in sorted(run.iterdir()):
        if path.is_file() and path.name != "run-manifest.json":
            files[path.name] = sec.file_sha(path)
    packet_hashes = {}
    component_hashes = {}
    if (run / "packets").is_dir():
        packet_hashes = {
            path.name: sec.file_sha(path) for path in sorted((run / "packets").iterdir())
        }
    if (run / "documents").is_dir():
        component_hashes = {
            path.name: sec.file_sha(path) for path in sorted((run / "documents").iterdir())
        }
    sec.write_new(
        run / "run-manifest.json",
        sec.json_bytes(
            {
                "kind": "BOUNDED_PARSER_DEVELOPMENT_RUN",
                "result": result,
                "files": files,
                "review_packet_hashes": packet_hashes,
                "original_component_hashes": component_hashes,
                "full_extraction_authorized": False,
                "event_database_frozen": False,
                "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
            }
        ),
    )


def main() -> int:
    if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode:
        print("Use python -I -S -B tools/corporate_events/parser_qa.py", file=sys.stderr)
        return 2
    args_parser = argparse.ArgumentParser(description=__doc__)
    args_parser.add_argument(
        "command", choices=("validate", "plan", "fetch", "parse", "record-review", "status")
    )
    args_parser.add_argument("--run", default="")
    args_parser.add_argument("--review-input", default="")
    args = args_parser.parse_args()
    try:
        proof = preflight()
        contact = sec.private_contact(ROOT)
        policy = json.loads((PROTOCOL / "parser-qa-policy.json").read_bytes())
        protocol = tomllib.loads((PROTOCOL / "event-data-protocol.toml").read_text("utf-8"))
        context = ssl.create_default_context() if args.command == "fetch" else None
        if context:
            context.minimum_version = ssl.TLSVersion.TLSv1_2
        install()
        PILOT.mkdir(parents=True, exist_ok=True)
        if args.command == "validate":
            result: dict[str, Any] = {
                "status": "PARSER_PILOT_PREFLIGHT_PASS",
                "contact": "CONFIGURED_PRIVATE",
                "metadata_census": "COMPLETE_VERIFIED",
                "scope": policy["scope"],
                "network_requests": 0,
                "full_extraction_authorized": False,
                "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
            }
        elif args.command == "plan":
            result = plan_pilot(proof, dict(protocol["source_registry"]["form_aliases"]))
        elif args.command == "status":
            result = {
                "plan_exists": (PILOT / "plan.json").exists(),
                "full_extraction_authorized": False,
                "gate": "CLOSED_FINAL_QA_FRAME_SCHEMA_AUTHORITY_AND_MANUAL_REVIEW_PENDING",
            }
        else:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", args.run) or args.run in {
                ".",
                "..",
            }:
                raise ValueError("PILOT_RUN_ID_INVALID")
            plan, plan_sha = read_plan()
            if args.command == "record-review":
                result = record_review(args.run, args.review_input)
            else:
                run = PILOT / "runs" / args.run
                if run.exists():
                    raise ValueError("IMMUTABLE_PILOT_RUN_ALREADY_EXISTS")
                run.mkdir(parents=True)
                if args.command == "fetch":
                    assert context is not None
                    agent = "CorporateEventAlphaResearch/phase2-acquisition-v1 (" + contact + ")"
                    result = acquire(run, plan, plan_sha, agent, context, proof)
                else:
                    result = parse_pilot(run, plan, plan_sha, proof)
                finalize_run(run, result)
        print(json.dumps(result), flush=True)
        return 0
    except (
        sec.AcquisitionError,
        sec.FirewallError,
        ValueError,
        OSError,
        sqlite3.DatabaseError,
        KeyError,
        TypeError,
    ) as error:
        reason = (
            str(error)
            if isinstance(error, (sec.AcquisitionError, sec.FirewallError, ValueError))
            else type(error).__name__
        )
        print(
            json.dumps(
                {
                    "status": "PARSER_PILOT_STOPPED",
                    "reason": reason,
                    "full_extraction_authorized": False,
                }
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
