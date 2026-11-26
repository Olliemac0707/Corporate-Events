"""Replay fixed SEC sources, prepare blinded controls and inventory clock evidence offline."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "protocols/corporate-event-alpha-v1"
PRIVATE = ROOT / "reports/corporate-event-alpha-v1/phase2-private"
OUTPUT = PRIVATE / "repurchase-source-audit-v1"
REGISTERED = {
    "tools/corporate_events/repurchase_source_audit.py",
    "tests/test_repurchase_source_audit.py",
    "protocols/corporate-event-alpha-v1/repurchase-source-audit-policy-v1.json",
}


def load(name: str, path: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


triage = load("source_audit_triage", "tools/corporate_events/repurchase_review_triage.py")
archive = load("source_audit_archive", "tools/corporate_events/sec_archive_membership.py")
sec, ledger = triage.sec, triage.ledger


def header_observations(raw: bytes) -> dict[str, Any]:
    boundary = re.search(rb"(?im)^<DOCUMENT>\s*\r?$", raw[:65536])
    if boundary is None:
        raise ValueError("AUDIT_SUBMISSION_HEADER_BOUNDARY_MISSING")
    header = raw[: boundary.start()]
    accept = re.findall(rb"(?im)^<ACCEPTANCE-DATETIME>([^\r\n<]+)", header)
    values = [value.decode("ascii", "replace").strip() for value in accept]
    valid = False
    if len(values) == 1 and re.fullmatch(r"[0-9]{14}", values[0]):
        try:
            datetime.strptime(values[0], "%Y%m%d%H%M%S")
            valid = True
        except ValueError:
            pass
    fields = {}
    for name, pattern in {
        "declared_document_count": rb"(?im)^PUBLIC DOCUMENT COUNT:\s*([0-9]+)",
        "filed_raw": rb"(?im)^FILED AS OF DATE:\s*([0-9]{8})",
        "form_raw": rb"(?im)^CONFORMED SUBMISSION TYPE:\s*([^\r\n]+)",
    }.items():
        match = re.search(pattern, header)
        fields[name] = match[1].decode("ascii", "replace").strip() if match else None
    ciks = re.findall(rb"(?im)^\s*CENTRAL INDEX KEY:\s*([0-9]{1,10})\s*$", header)
    return {
        **fields,
        "acceptance_raw_values": values,
        "acceptance_calendar_valid": valid,
        "acceptance_datetime_et": None,
        "acceptance_datetime_utc": None,
        "source_timezone_semantics_verified": False,
        "header_ciks": sorted({str(int(cik)) for cik in ciks}),
        "incorporation_codes_as_stated": [
            x.decode("ascii", "replace").strip()
            for x in re.findall(rb"(?im)^\s*STATE OF INCORPORATION:\s*([^\r\n]+)", header)
        ],
        "verified_dissemination_datetime": None,
        "registered_public_datetime": None,
        "public_time_basis": "TIMESTAMP_UNRESOLVED",
        "historical_security_eligibility_certified": False,
    }


def control_cues(text: str, cap: int = 250) -> dict[str, Any]:
    if not 0 < cap <= 250:
        raise ValueError("AUDIT_CUE_CAP_INVALID")
    patterns = {
        "REPURCHASE_OR_BUYBACK": r"\brepurchas\w*|\bbuy[ -]?back\b",
        "COMMON_SHARE_ACQUISITION_WORDING": (
            r"\b(?:acquir\w*|purchas\w*)\b[^\n.]{0,160}"
            r"\b(?:outstanding\s+)?common\s+(?:stock|shares)\b"
        ),
        "BOARD_AUTHORITY_AND_COMMON_SHARES": (
            r"\bboard\b[^\n.]{0,160}\b(?:approv\w*|authoriz\w*)\b"
            r"[^\n.]{0,240}\bcommon\s+(?:stock|shares)\b"
        ),
    }
    hits = sorted(
        (match.start(), match.end(), code)
        for code, pattern in patterns.items()
        for match in re.finditer(pattern, text, re.I)
    )
    retained = []
    for start, end, code in hits[:cap]:
        a, b = max(0, start - 160), min(len(text), end + 240)
        quote = text[a:b]
        retained.append(
            {
                "cue": code,
                "text_start": a,
                "text_end": b,
                "quote": quote,
                "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
            }
        )
    return {
        "total_cue_hits": len(hits),
        "retained_cue_hits": len(retained),
        "cue_excerpt_cap_reached": len(hits) > cap,
        "cues": retained,
        "event_presence_assessed": False,
        "event_absence_certified": False,
    }


def archive_matches(headers: list[dict[str, Any]], accessions: set[str]) -> list[str]:
    matches = set()
    for header in headers:
        match = re.fullmatch(r"(?:\./)?([0-9]{10}-[0-9]{2}-[0-9]{6})\.nc", header["name"])
        if match and match[1] in accessions:
            matches.add(match[1])
    return sorted(matches)


def reviewer_sources(
    sources: list[dict[str, Any]], cases: list[dict[str, Any]], seed: str
) -> list[dict[str, Any]]:
    if len(cases) > 30:
        raise ValueError("ACTION_CONTEXT_PACKET_CAP_EXCEEDED")
    positive_sources = {c["accession"] for c in cases}
    fixed_controls = {s["accession"] for s in sources if s["role"] == "SOURCE_CONTROL"}
    selected = positive_sources | fixed_controls
    available = {s["accession"] for s in sources}
    if len(available) != len(sources) or not selected <= available or len(fixed_controls) != 20:
        raise ValueError("REVIEW_FRAME_SOURCE_MEMBERSHIP_INVALID")
    return sorted(
        [s for s in sources if s["accession"] in selected],
        key=lambda s: hashlib.sha256(f"{seed}|{s['accession']}".encode()).hexdigest(),
    )


def build() -> dict[str, Any]:
    _, baseline, _ = triage.preflight()
    registry = json.loads((PROTOCOL / "repurchase-source-audit-extension-v1.json").read_bytes())
    if set(registry["files"]) != REGISTERED:
        raise ValueError("SOURCE_AUDIT_REGISTRY_SCOPE_INVALID")
    for name, digest in registry["files"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("SOURCE_AUDIT_REGISTERED_FILE_CHANGED")
    policy = json.loads((PROTOCOL / "repurchase-source-audit-policy-v1.json").read_bytes())
    if any(
        policy[k] is not False
        for k in (
            "network_allowed",
            "market_data_allowed",
            "gate_opening_allowed",
            "acceptance_is_dissemination",
            "zero_cues_certify_negative_control",
        )
    ):
        raise ValueError("SOURCE_AUDIT_CLOSED_GATES_REQUIRED")
    archive_registry = json.loads(
        (PROTOCOL / "offline-feed-prefix-verifier-extension-v1.json").read_bytes()
    )
    for name, digest in archive_registry["files"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("SOURCE_AUDIT_ARCHIVE_VERIFIER_CHANGED")
    sec.EXTENSION_FILES = (
        *sec.EXTENSION_FILES,
        *REGISTERED,
        *triage.REGISTERED,
        *triage.context.REGISTERED,
        *ledger.REGISTERED,
        *baseline["parser_files"],
        *archive_registry["files"],
    )
    sec.install_firewall()
    sys.addaudithook(
        lambda event, args: (
            (_ for _ in ()).throw(RuntimeError("SOURCE_AUDIT_OFFLINE_ONLY"))
            if event.startswith(("socket.", "http.client.", "subprocess."))
            else None
        )
    )
    for name, digest in policy["private_inputs"].items():
        path = (PRIVATE / name).resolve()
        if not path.is_relative_to(PRIVATE.resolve()) or sec.file_sha(path) != digest:
            raise ValueError("SOURCE_AUDIT_INPUT_CHANGED")
    inventory = json.loads((ledger.acq.OUTPUT / "acquired-source-manifest.json").read_bytes())
    metadata = json.loads((ledger.acq.DISCOVERY / "bounded-source-manifest.json").read_bytes())
    entries = {e["accession"]: e for e in metadata["entries"]}
    coverage = json.loads((ledger.OUTPUT / "source-coverage.json").read_bytes())
    by_acc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for component in coverage["components"]:
        by_acc[component["accession"]].append(component)
    old_manifest = json.loads((ledger.OUTPUT / "output-manifest.json").read_bytes())
    for name, digest in old_manifest["files"].items():
        path = (ledger.OUTPUT / name).resolve()
        if not path.is_relative_to(ledger.OUTPUT.resolve()) or sec.file_sha(path) != digest:
            raise ValueError("SOURCE_AUDIT_LEDGER_REPLAY_FAILED")
    headers, components, controls = [], [], []
    cache = sec.Cache(ledger.acq.OUTPUT)
    for source in inventory["sources"]:
        acc = source["accession"]
        entry = entries[acc]
        raw = cache.read(source["sha256"])
        ledger.acq.check_submission(raw, entry)
        documents, _ = ledger.rules.p.base.resolve_submission(raw, acc)
        original = {c["component_id"]: c for c in by_acc[acc]}
        if len(documents) != len(original):
            raise ValueError("SOURCE_AUDIT_COMPONENT_COUNT_MISMATCH")
        header = header_observations(raw)
        header.update(
            accession=acc,
            year=source["year"],
            form=entry["form"],
            source_role=source["role"],
            container_sha256=source["sha256"],
            historical_index_filer_cik=entry["cik_from_historical_index"],
            filer_cik_header_match=str(int(entry["cik_from_historical_index"]))
            in header["header_ciks"],
        )
        if not header["filer_cik_header_match"]:
            raise ValueError("SOURCE_AUDIT_HISTORICAL_FILER_MISMATCH")
        declared = header["declared_document_count"]
        header["declared_vs_replayed_document_count_match"] = (
            int(declared) == len(documents) if declared is not None else None
        )
        headers.append(header)
        component_rows, cue_rows = [], []
        for index, doc in enumerate(documents, 1):
            key = ledger.rules.p.base.stable(acc, index, doc.start, doc.end, doc.sha256)
            c = original[key]
            if (
                doc.start != c["container_byte_start"]
                or doc.end != c["container_byte_end"]
                or sec.sha(raw[doc.start : doc.end]) != c["raw_document_sha256"]
            ):
                raise ValueError("SOURCE_AUDIT_RAW_COMPONENT_MISMATCH")
            rendered = ledger.rules.render(doc)
            text = rendered["text"]
            if (
                rendered["status"] != c["extraction_status"]
                or sec.sha(text.encode()) != c["derived_text_sha256"]
            ):
                raise ValueError("SOURCE_AUDIT_RENDER_REPLAY_MISMATCH")
            component_rows.append(
                {
                    "component_id": key,
                    "accession": acc,
                    "extraction_status": c["extraction_status"],
                    "source_role": source["role"],
                    "raw_and_render_replay": "PASS",
                }
            )
            if source["role"] == "SOURCE_CONTROL" and text:
                cues = control_cues(text)
                cue_rows.append({"component_id": key, "filename": c["filename"], **cues})
        components.extend(component_rows)
        if source["role"] == "SOURCE_CONTROL":
            statuses = dict(Counter(c["extraction_status"] for c in component_rows))
            controls.append(
                {
                    "accession": acc,
                    "year": source["year"],
                    "components": len(component_rows),
                    "component_statuses": statuses,
                    "total_cue_hits": sum(c["total_cue_hits"] for c in cue_rows),
                    "components_with_cues": cue_rows,
                    "independent_source_review_complete": False,
                    "negative_control_certified": False,
                }
            )
    prefix = (PRIVATE / "archive-membership-probe-v1/compressed-prefix.bin").read_bytes()
    chunks, offset = [prefix], len(prefix)
    ranges: list[dict[str, Any]] = []
    receipt_path = PRIVATE / "archive-membership-probe-v2/requests.jsonl"
    for line in receipt_path.read_bytes().splitlines():
        r = json.loads(line)
        match = re.fullmatch(r"bytes=([0-9]+)-([0-9]+)", r["range"])
        if not match or int(match[1]) != offset:
            raise ValueError("SOURCE_AUDIT_ARCHIVE_RANGE_GAP")
        path = (PRIVATE / "archive-membership-probe-v2" / r["raw_locator"]).resolve()
        if not path.is_relative_to((PRIVATE / "archive-membership-probe-v2").resolve()):
            raise ValueError("SOURCE_AUDIT_ARCHIVE_RANGE_PATH_INVALID")
        body = path.read_bytes()
        if len(body) != int(match[2]) - offset + 1 or sec.sha(body) != r["sha256"]:
            raise ValueError("SOURCE_AUDIT_ARCHIVE_RANGE_CHANGED")
        chunks.append(body)
        offset = int(match[2]) + 1
        ranges.append({"range": r["range"], "sha256": r["sha256"]})
    replay = archive.inspect_prefix(chunks, policy["archive_probe_accession"])
    old_replay = json.loads(
        (PRIVATE / "authority-evidence-v2/archive-offline-replay.json").read_bytes()
    )
    if any(replay[k] != old_replay[k] for k in replay):
        raise ValueError("SOURCE_AUDIT_ARCHIVE_REPLAY_MISMATCH")
    exact_matches = archive_matches(replay["member_headers"], set(entries))
    # Membership is not certified timing without the registered semantic authority.
    archive_result = {
        "archive_url": policy["archive_url"],
        "archive_date_label": "2022-07-26",
        "ranges": ranges,
        "compressed_bytes": replay["compressed_bytes"],
        "verified_member_headers": replay["member_headers_seen"],
        "gzip_eof": replay["gzip_eof_and_checksum_available"],
        "pilot_accession_matches": exact_matches,
        "absence_inference_allowed": False,
        "public_time_certified": False,
    }
    cases = [
        json.loads(line)
        for line in (triage.context.OUTPUT / "actions.jsonl").read_bytes().splitlines()
    ]
    selected = reviewer_sources(inventory["sources"], cases, policy["review_order_seed"])
    packets = []
    index_links = []
    for source in selected:
        acc = source["accession"]
        packet_id = sec.sha(f"{policy['review_order_seed']}|{acc}".encode())
        parts = []
        for c in by_acc[acc]:
            text_file = (ledger.OUTPUT / c["derived_text_file"]).resolve()
            if not text_file.is_file():
                raise ValueError("SOURCE_AUDIT_REVIEW_TEXT_LINK_MISSING")
            link = "../../bounded-repurchase-ledger-v3/" + c["derived_text_file"]
            parts.append(
                {
                    "filename": c["filename"],
                    "extraction_status": c["extraction_status"],
                    "complete_text_file": link,
                    "container_byte_start": c["container_byte_start"],
                    "container_byte_end": c["container_byte_end"],
                }
            )
        raw_link = "../../repurchase-acquisition-v1/" + source["object_locator"]
        packets.append(
            {
                "packet_id": packet_id,
                "accession": acc,
                "form": entries[acc]["form"],
                "filing_date_metadata": entries[acc]["filed"],
                "original_container_file": raw_link,
                "components": parts,
                "source_completeness_certified": False,
            }
        )
        links = "".join(
            '<li><a href="'
            + escape(c["complete_text_file"], quote=True)
            + '">'
            + escape(c["filename"])
            + "</a> — "
            + escape(c["extraction_status"])
            + "</li>"
            for c in parts
        )
        page = (
            '<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" '
            "content=\"default-src 'none'; style-src 'unsafe-inline'\">"
            "<title>Full-source factual review</title><h1>Source "
            + packet_id[:12]
            + "</h1><p>Read all components and identify actions, date roles, amounts, "
            "share classes and document relationships. Inspect retained non-text content "
            "where required; empty extracted text is not evidence of absence. "
            "Record missing context and uncertainty. "
            "Filing dates do not prove public timing.</p>"
            "<p>Accession "
            + escape(acc)
            + '</p><p><a href="'
            + raw_link
            + '">Original complete submission</a></p><ul>'
            + links
            + "</ul>"
        )
        ledger.immutable(OUTPUT / "reviewer" / (packet_id + ".html"), page.encode())
        index_links.append(
            '<li><a href="' + packet_id + '.html">Source ' + packet_id[:12] + "</a></li>"
        )
    index_page = (
        '<!doctype html><meta charset="utf-8"><title>Full-source review packet</title>'
        "<h1>Full-source review packet</h1><p>Prepared but not evaluated. No reviewer "
        "is assigned and no decisions have been recorded. Classification proposals, "
        "risk scores, prior owner answers and sampling roles are withheld.</p><ol>"
        + "".join(index_links)
        + "</ol>"
    )
    ledger.immutable(OUTPUT / "reviewer/index.html", index_page.encode())

    def write(name: str, value: Any) -> None:
        ledger.immutable(OUTPUT / name, sec.json_bytes(value))

    write("header-timing-observations.json", {"filings": headers})
    write("component-replay.json", {"components": components})
    write("control-source-audit.json", {"controls": controls})
    write("archive-evidence-replay.json", archive_result)
    write(
        "reviewer-packets.json",
        {
            "packets": packets,
            "reviewer": None,
            "decisions": 0,
            "predictions_included": False,
            "sampling_roles_included": False,
        },
    )
    write(
        "blank-review-decisions.json",
        {
            "reviewer_id": None,
            "records": [
                {
                    "packet_id": q["packet_id"],
                    "source_inspected": None,
                    "findings": None,
                    "evidence": None,
                    "source_coverage": None,
                    "uncertainty": None,
                }
                for q in packets
            ],
        },
    )
    by_header = {h["accession"]: h for h in headers}
    clock_rows = [
        {
            "case_key": c["case_key"],
            "accession": c["accession"],
            "stated_announcement_date": c["facts"]["stated_announcement_date"],
            "source_filed_metadata": c["source_filed"],
            "acceptance_raw_values": by_header[c["accession"]]["acceptance_raw_values"],
            "verified_dissemination_datetime": None,
            "registered_public_datetime": None,
            "status": "TIMESTAMP_UNRESOLVED",
            "required_evidence": (
                "Accession-specific registered official dissemination-day/time evidence; "
                "acceptance and printed release dates are insufficient."
            ),
        }
        for c in cases
    ]
    write("action-clock-worklist.json", {"actions": clock_rows})
    summary = {
        "version": "repurchase-source-audit-v1",
        "status": "SOURCE_INTEGRITY_PASS_GATES_CLOSED",
        "submissions_replayed": len(headers),
        "components_replayed": len(components),
        "component_statuses": dict(Counter(c["extraction_status"] for c in components)),
        "fixed_control_filings": len(controls),
        "fixed_control_components": sum(c["components"] for c in controls),
        "control_component_statuses": dict(
            Counter(
                c["extraction_status"] for c in components if c["source_role"] == "SOURCE_CONTROL"
            )
        ),
        "controls_with_crosscheck_cues": sum(c["total_cue_hits"] > 0 for c in controls),
        "total_control_crosscheck_cues": sum(c["total_cue_hits"] for c in controls),
        "negative_controls_certified": 0,
        "headers_with_one_calendar_valid_acceptance": sum(
            h["acceptance_calendar_valid"] for h in headers
        ),
        "acceptance_timezones_independently_verified": 0,
        "archive_member_headers_replayed": replay["member_headers_seen"],
        "pilot_accessions_in_cached_archive_prefix": len(exact_matches),
        "public_times_certified": 0,
        "registered_action_clocks_ready": 0,
        "review_packets_prepared": len(packets),
        "reviewer_assigned": False,
        "independent_full_source_reviews_completed": 0,
        "action_records_retained": len(cases),
        "research_qualifying_events": 0,
        "security_mappings_queried": 0,
        "market_data_accessed": False,
        "new_network_requests": 0,
        "original_718_sample_changes": 0,
        "full_extraction_authorized": False,
        "final_qa_authorized": False,
        "phase3_authorized": False,
        "regressions_authorized": False,
        "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
    }
    write("summary.json", summary)
    write(
        "output-manifest.json",
        {
            "files": {
                str(path.relative_to(OUTPUT)).replace("\\", "/"): sec.file_sha(path)
                for path in OUTPUT.rglob("*")
                if path.is_file() and path.name != "output-manifest.json"
            },
            "code_registry": registry,
            "input_pins": policy["private_inputs"],
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
        print(json.dumps({"status": "SOURCE_AUDIT_STOPPED", "reason": str(error)}), flush=True)
        raise SystemExit(2) from None
