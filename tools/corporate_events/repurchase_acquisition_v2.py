"""Acquire only the frozen repurchase pilot submissions; no event extraction."""

from __future__ import annotations

import argparse
import http.client
import importlib.util
import json
import re
import ssl
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "reports/corporate-event-alpha-v1/phase2-private"
PROTOCOL = ROOT / "protocols/corporate-event-alpha-v1"
DISCOVERY = PRIVATE / "repurchase-discovery-v1"
OUTPUT = PRIVATE / "repurchase-acquisition-v1"
SOURCE_SHA = "4a92feb81b8701950a662b419c4fb359fa2da780469e28a70055459076bb078b"
BYTE_CAP = 536870912
REQUEST_CAP = 500
REGISTERED = {
    "tools/corporate_events/repurchase_acquisition_v2.py",
    "tools/corporate_events/sec_document_client.py",
    "tests/test_repurchase_acquisition_v2.py",
    "protocols/corporate-event-alpha-v1/repurchase-acquisition-policy-v2.json",
}


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools/corporate_events" / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


discovery = load("repurchase_acquisition_discovery", "sec_discovery_audit_v2.py")
docs = load("repurchase_acquisition_documents", "sec_document_client.py")
sec = discovery.sec


def check_submission(raw: bytes, entry: dict[str, Any]) -> dict[str, Any]:
    docs.validate_payload(raw, entry["source_url"])
    header = re.split(rb"(?im)^<DOCUMENT>\s*\r?$", raw[:65536], maxsplit=1)
    if len(header) != 2:
        raise ValueError("SUBMISSION_HEADER_BOUNDARY_MISSING_OR_OVER_BOUND")
    fields = {}
    for name, pattern in {
        "accession": rb"(?im)^ACCESSION NUMBER:\s*([0-9]{10}-[0-9]{2}-[0-9]{6})\s*$",
        "form": rb"(?im)^CONFORMED SUBMISSION TYPE:\s*([^\r\n]+)",
        "filed": rb"(?im)^FILED AS OF DATE:\s*([0-9]{8})\s*$",
    }.items():
        found = re.search(pattern, header[0])
        if found is None:
            raise ValueError("SUBMISSION_REQUIRED_HEADER_MISSING_" + name.upper())
        fields[name] = found[1].decode("ascii").strip()
    filed = datetime.strptime(fields["filed"], "%Y%m%d").date().isoformat()
    if (fields["accession"], fields["form"], filed) != (
        entry["accession"],
        entry["form"],
        entry["filed"],
    ):
        raise ValueError("SUBMISSION_IDENTITY_FORM_OR_DATE_MISMATCH")
    ciks = {
        str(int(x))
        for x in re.findall(rb"(?im)^\s*CENTRAL INDEX KEY:\s*([0-9]{1,10})\s*$", header[0])
    }
    if str(int(entry["cik_from_historical_index"])) not in ciks:
        raise ValueError("SUBMISSION_HISTORICAL_FILER_CIK_MISMATCH")
    terminal = re.search(rb"(?im)^</SEC-DOCUMENT>([^\S\r\n]*)\r?\n?([\s\S]*)\Z", raw)
    terminal_ok = False
    if terminal:
        trailer = terminal[2].strip()
        terminal_ok = not trailer or (
            trailer == b"-----END PRIVACY-ENHANCED MESSAGE-----"
            and raw.startswith(b"-----BEGIN PRIVACY-ENHANCED MESSAGE-----")
        )
    starts = len(re.findall(rb"(?im)^<DOCUMENT>\s*\r?$", raw))
    ends = len(re.findall(rb"(?im)^</DOCUMENT>\s*\r?$", raw))
    primary = re.search(rb"(?im)^<TYPE>([^\r\n]+)", header[1])
    if (
        not starts
        or starts != ends
        or not terminal_ok
        or primary is None
        or primary[1].decode("ascii").strip() != entry["form"]
    ):
        raise ValueError("SUBMISSION_INCOMPLETE_CONTAINER_OR_PRIMARY_FORM")
    return {
        "accession": fields["accession"],
        "form": fields["form"],
        "filed": filed,
        "historical_filer_CIK_matched": True,
        "issuer_identity_certified": False,
        "document_blocks": starts,
        "public_dissemination_time_certified": False,
        "event_extraction_performed": False,
    }


def preflight() -> tuple[dict[str, Any], dict[str, Any]]:
    proof = discovery.m.verify_registration()
    discovery.verify_audit_registration()
    registry = json.loads((PROTOCOL / "repurchase-acquisition-extension-v2.json").read_bytes())
    if set(registry["files"]) != REGISTERED:
        raise ValueError("ACQUISITION_REGISTRY_SCOPE_MISMATCH")
    for name, expected in registry["files"].items():
        if sec.file_sha(ROOT / name) != expected:
            raise ValueError("ACQUISITION_REGISTERED_FILE_CHANGED")
    predecessor = json.loads((PROTOCOL / "repurchase-acquisition-extension-v1.json").read_bytes())
    for name, expected in predecessor["files"].items():
        if sec.file_sha(ROOT / name) != expected:
            raise ValueError("ACQUISITION_PREDECESSOR_CHANGED")
    policy = json.loads((PROTOCOL / "repurchase-acquisition-policy-v2.json").read_bytes())
    path = DISCOVERY / "bounded-source-manifest.json"
    if sec.file_sha(path) != SOURCE_SHA or policy["source_manifest_sha256"] != SOURCE_SHA:
        raise ValueError("FROZEN_SOURCE_MANIFEST_CHANGED")
    audit_path = DISCOVERY / "metadata-manifest-audit-v2.json"
    if sec.file_sha(audit_path) != policy["metadata_audit_sha256"]:
        raise ValueError("METADATA_AUDIT_CHANGED")
    audit = json.loads(audit_path.read_bytes())
    if (
        audit["status"] != "RAW_METADATA_REPLAY_AND_SOURCE_SELECTION_PASSED"
        or audit["manifest_sha256"] != SOURCE_SHA
    ):
        raise ValueError("METADATA_AUDIT_NOT_PASSED")
    for name, expected in audit["month_file_sha256"].items():
        if sec.file_sha(DISCOVERY / "months" / name) != expected:
            raise ValueError("AUDITED_MONTH_METADATA_CHANGED")
    binding = json.loads((DISCOVERY / "bounded-source-manifest-binding.json").read_bytes())
    if binding["manifest_sha256"] != SOURCE_SHA:
        raise ValueError("SOURCE_MANIFEST_BINDING_MISMATCH")
    manifest = json.loads(path.read_bytes())
    discovery.verify_selection(manifest)
    allowed = {x["source_url"] for x in manifest["entries"]}
    if len(allowed) != 100:
        raise ValueError("EXACTLY_100_SOURCE_URLS_REQUIRED")
    for entry in manifest["entries"]:
        docs.validate_url(entry["source_url"], allowed)
    return manifest, {
        "discovery": proof,
        "acquisition_registry": registry,
        "source_manifest_sha256": SOURCE_SHA,
        "metadata_audit_sha256": sec.file_sha(audit_path),
    }


class Client:
    """Persistent request/byte/429 limits with the original shared attempt ledger."""

    def __init__(
        self,
        root: Path,
        entries: list[dict[str, Any]],
        scheduler: Any,
        agent: str,
        transport: Any = None,
    ) -> None:
        self.root, self.scheduler, self.agent = root, scheduler, agent
        self.entries = {x["source_url"]: x for x in entries}
        self.cache = sec.Cache(root)
        self.state_path = root / "request-budget.json"
        byte_path = root / "byte-budget.json"
        identity = {
            "source_manifest_sha256": SOURCE_SHA,
            "byte_cap": BYTE_CAP,
            "request_cap": REQUEST_CAP,
        }
        marker = root / "namespace-identity.json"
        if marker.exists():
            if (
                json.loads(marker.read_bytes()) != identity
                or not self.state_path.exists()
                or not byte_path.exists()
            ):
                raise ValueError("ACQUISITION_NAMESPACE_OR_BUDGET_MISSING_NO_RESET")
        else:
            if any(root.glob("*budget.json")) or self.cache.registry.exists():
                raise ValueError("ACQUISITION_ORPHANED_STATE_NO_RESET")
            root.mkdir(parents=True, exist_ok=True)
            sec.write_new(marker, sec.json_bytes(identity))
            sec.write_new(
                self.state_path,
                sec.json_bytes({"requests": 0, "request_cap": REQUEST_CAP, "consecutive_429": 0}),
            )
            docs.ByteBudget(byte_path, BYTE_CAP, SOURCE_SHA).charge(0)
        self.state = json.loads(self.state_path.read_bytes())
        if (
            self.state["request_cap"] != REQUEST_CAP
            or type(self.state["requests"]) is not int
            or not 0 <= self.state["requests"] <= REQUEST_CAP
            or type(self.state["consecutive_429"]) is not int
            or not 0 <= self.state["consecutive_429"] <= 2
        ):
            raise ValueError("ACQUISITION_REQUEST_BUDGET_INVALID")
        if self.state["consecutive_429"] >= 2:
            raise ValueError("TWO_CONSECUTIVE_429_STOP_BATCH")
        self.budget = docs.ByteBudget(byte_path, BYTE_CAP, SOURCE_SHA)
        if type(json.loads(byte_path.read_bytes())["received_body_bytes"]) is not int:
            raise ValueError("ACQUISITION_BYTE_BUDGET_INVALID")
        plan_path = root / "request-plan.jsonl"
        if plan_path.exists() and len(plan_path.read_bytes().splitlines()) > self.state["requests"]:
            raise ValueError("ACQUISITION_REQUEST_BUDGET_ROLLBACK")
        receipts_path = root / "requests.jsonl"
        if (
            receipts_path.exists()
            and sum(
                json.loads(x)["received_bytes"] for x in receipts_path.read_bytes().splitlines()
            )
            > self.budget.spent
        ):
            raise ValueError("ACQUISITION_BYTE_BUDGET_ROLLBACK")
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.transport = transport or docs.HttpsDocuments(
            sec, set(self.entries), context, self.budget
        )

    def save(self) -> None:
        self.state_path.write_bytes(sec.json_bytes(self.state))

    def fetch(self, url: str) -> tuple[bytes, dict[str, Any]]:
        docs.validate_url(url, set(self.entries))
        if self.scheduler.state().get("blocked_reason"):
            raise ValueError("BATCH_BLOCKED_REQUIRES_REVIEW")
        cached = self.cache.latest(url)
        if cached is None:
            quarantined = self.cache.latest("quarantine:" + url)
            if quarantined is not None:
                raw = self.cache.read(quarantined["sha256"])
                receipts = [
                    json.loads(line)
                    for line in (self.root / "requests.jsonl").read_bytes().splitlines()
                ]
                matching = [
                    row
                    for row in receipts
                    if row.get("url") == url
                    and row.get("status") == 200
                    and row.get("response_sha256") == quarantined["sha256"]
                ]
                if (
                    not matching
                    or any(
                        row["headers"].get("content-encoding", "identity") != "identity"
                        for row in matching
                    )
                    or quarantined["content_type"]
                    not in {"text/plain", "text/html", "application/octet-stream"}
                ):
                    raise ValueError("QUARANTINE_TRANSPORT_PROFILE_NOT_REVALIDATABLE")
                check_submission(raw, self.entries[url])
                cached = self.cache.store(url, raw, quarantined["content_type"])
                sec.append_record(
                    self.root / "cache-promotions.jsonl",
                    {
                        "url": url,
                        "sha256": cached["sha256"],
                        "reason": "HEADER_AND_PEM_FRAMING_REVALIDATED_WITHOUT_NEW_REQUEST",
                        "budget_reset": False,
                    },
                )
        if cached:
            raw = self.cache.read(cached["sha256"])
            if cached["content_type"] not in {
                "text/plain",
                "text/html",
                "application/octet-stream",
            }:
                raise ValueError("ACQUISITION_CACHED_CONTENT_TYPE_INVALID")
            check_submission(raw, self.entries[url])
            return raw, cached
        used = self.scheduler.attempts_used(url)
        if not 0 <= used < 5:
            raise ValueError("ACQUISITION_FIVE_ATTEMPT_LIMIT")
        for attempt in range(used + 1, 6):
            if self.state["requests"] >= REQUEST_CAP:
                raise ValueError("ACQUISITION_REQUEST_CAP_EXHAUSTED")
            self.budget.cap()
            started = self.scheduler.start(time.time() + 300)
            self.scheduler.record_attempt(url)
            self.state["requests"] += 1
            self.save()
            request = {
                "url": url,
                "attempt": attempt,
                "request_start_epoch": started,
                "agent_sha256": sec.sha(self.agent.encode()),
            }
            sec.append_record(self.root / "request-plan.jsonl", request)
            try:
                response = self.transport.get(url, self.agent)
            except (OSError, http.client.HTTPException, ValueError, sec.AcquisitionError) as error:
                local = isinstance(error, (ValueError, sec.AcquisitionError, sec.FirewallError))
                sec.append_record(
                    self.root / "requests.jsonl",
                    {
                        **request,
                        "status": None,
                        "received_bytes": getattr(self.transport, "last_bytes", 0),
                        "error_type": type(error).__name__,
                        "reason": "LOCAL_OR_PAYLOAD_STOP" if local else "TRANSPORT_FAILURE",
                    },
                )
                if local or attempt == 5:
                    raise
                self.scheduler.hold(2 ** (attempt - 1), "ACQUISITION_TRANSPORT_BACKOFF")
                continue
            sec.append_record(
                self.root / "requests.jsonl",
                {
                    **request,
                    "status": response.status,
                    "received_bytes": len(response.body),
                    "response_sha256": sec.sha(response.body),
                    "headers": {
                        k: v
                        for k, v in response.headers.items()
                        if k
                        in {
                            "content-type",
                            "content-length",
                            "content-encoding",
                            "retry-after",
                            "location",
                        }
                    },
                },
            )
            if response.status == 403:
                self.scheduler.block("HTTP_403_STOP_NO_EVASION")
                raise ValueError("HTTP_403_STOP_NO_EVASION")
            self.state["consecutive_429"] = (
                self.state["consecutive_429"] + 1 if response.status == 429 else 0
            )
            self.save()
            if response.status in {408, 429, 500, 502, 503, 504}:
                delay = max(
                    2 ** (attempt - 1),
                    sec.retry_after_seconds(response.headers.get("retry-after"), sec.utc_now()),
                )
                self.scheduler.hold(delay, "ACQUISITION_RETRY_AFTER_OR_BACKOFF")
                if self.state["consecutive_429"] >= 2:
                    raise ValueError("TWO_CONSECUTIVE_429_STOP_BATCH")
                continue
            if response.status != 200:
                raise ValueError("ACQUISITION_HTTP_STOP_" + str(response.status))
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if (
                content_type not in {"text/plain", "text/html", "application/octet-stream"}
                or response.headers.get("content-encoding", "identity") != "identity"
            ):
                self.cache.store("quarantine:" + url, response.body, content_type)
                raise ValueError("ACQUISITION_CONTENT_TYPE_OR_ENCODING_INVALID")
            try:
                check_submission(response.body, self.entries[url])
            except ValueError as error:
                self.cache.store("quarantine:" + url, response.body, content_type)
                if str(error) == "SEC_BLOCKED_PAGE_STOP_REVIEW":
                    self.scheduler.block(str(error))
                raise
            record = self.cache.store(url, response.body, content_type)
            self.scheduler.record_success(url)
            return response.body, record
        raise ValueError("ACQUISITION_FIVE_ATTEMPT_LIMIT")


def old_budget_hashes() -> dict[str, str]:
    return {p.name: sec.file_sha(p) for p in sorted(DISCOVERY.glob("*-budget.json"))}


def acquire() -> dict[str, Any]:
    manifest, proof = preflight()
    agent = sec.private_contact(ROOT)
    prior = old_budget_hashes()
    sec.ALLOWED_HOSTS = frozenset({"www.sec.gov"})
    sec.EXTENSION_FILES = (*sec.EXTENSION_FILES, *REGISTERED)
    sec.install_firewall()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with sec.Scheduler(PRIVATE) as scheduler:
        client = Client(OUTPUT, manifest["entries"], scheduler, agent)
        sec.append_record(
            OUTPUT / "preflights.jsonl",
            {**proof, "private_contact": "VALID_VALUE_NOT_LOGGED", "old_discovery_budgets": prior},
        )
        receipts_path = OUTPUT / "acquired-sources.jsonl"
        previous = (
            {
                x["accession"]: x
                for x in [json.loads(line) for line in receipts_path.read_bytes().splitlines()]
            }
            if receipts_path.exists()
            else {}
        )
        sources = []
        for entry in manifest["entries"]:
            raw, record = client.fetch(entry["source_url"])
            receipt = {
                "accession": entry["accession"],
                "year": entry["year"],
                "role": entry["role"],
                "url": entry["source_url"],
                "sha256": record["sha256"],
                "bytes": len(raw),
                "object_locator": record["object_locator"],
                "container_validation": check_submission(raw, entry),
            }
            if entry["accession"] in previous:
                if previous[entry["accession"]] != receipt:
                    raise ValueError("ACQUIRED_SOURCE_RECEIPT_CHANGED")
            else:
                sec.append_record(receipts_path, receipt)
            sources.append(receipt)
            print(
                json.dumps(
                    {
                        "completed": len(sources),
                        "target": 100,
                        "HTTP_requests": client.state["requests"],
                        "received_body_bytes": client.budget.spent,
                    }
                ),
                flush=True,
            )
        if old_budget_hashes() != prior:
            raise ValueError("OLD_DISCOVERY_BUDGET_CHANGED")
        inventory = {
            "version": "bounded-repurchase-acquisition-v1",
            "status": "ALL_100_SUBMISSIONS_ACQUIRED_AND_CONTAINER_VALIDATED",
            "source_manifest_sha256": SOURCE_SHA,
            "source_count": len(sources),
            "sources": sources,
            "received_body_bytes": client.budget.spent,
            "received_body_byte_cap": BYTE_CAP,
            "HTTP_requests": client.state["requests"],
            "HTTP_request_cap": REQUEST_CAP,
            "selected_forms": dict(Counter(x["container_validation"]["form"] for x in sources)),
            "code_registry": proof["acquisition_registry"],
            "old_discovery_budgets": prior,
            "event_extraction_started": False,
            "research_events_accepted": 0,
            "original_718_sample_changes": 0,
            "full_extraction_authorized": False,
            "final_qa_authorized": False,
            "phase3_authorized": False,
            "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
        }
        discovery.m.immutable(OUTPUT / "acquired-source-manifest.json", inventory)
        discovery.m.immutable(
            OUTPUT / "acquired-source-manifest-binding.json",
            {
                "manifest_sha256": sec.file_sha(OUTPUT / "acquired-source-manifest.json"),
                "frozen_source_manifest_sha256": SOURCE_SHA,
            },
        )
        return inventory


def audit() -> dict[str, Any]:
    manifest, proof = preflight()
    sys.addaudithook(
        lambda event, args: (
            (_ for _ in ()).throw(RuntimeError("ACQUISITION_AUDIT_OFFLINE_ONLY"))
            if event.startswith(("socket.", "http.client."))
            else None
        )
    )
    path = OUTPUT / "acquired-source-manifest.json"
    inventory = json.loads(path.read_bytes())
    if (
        inventory["status"] != "ALL_100_SUBMISSIONS_ACQUIRED_AND_CONTAINER_VALIDATED"
        or inventory["source_count"] != 100
        or inventory["source_manifest_sha256"] != SOURCE_SHA
        or inventory["original_718_sample_changes"] != 0
        or inventory["firewall_counters"] != dict.fromkeys(sec.FIREWALL_COUNTERS, 0)
        or any(
            inventory[name] is not False
            for name in (
                "event_extraction_started",
                "full_extraction_authorized",
                "final_qa_authorized",
                "phase3_authorized",
            )
        )
    ):
        raise ValueError("ACQUISITION_STATUS_OR_CLOSED_GATE_MISMATCH")
    binding = json.loads((OUTPUT / "acquired-source-manifest-binding.json").read_bytes())
    if binding != {
        "manifest_sha256": sec.file_sha(path),
        "frozen_source_manifest_sha256": SOURCE_SHA,
    }:
        raise ValueError("ACQUIRED_MANIFEST_BINDING_MISMATCH")
    entries = {x["accession"]: x for x in manifest["entries"]}
    if len(inventory["sources"]) != 100 or {x["accession"] for x in inventory["sources"]} != set(
        entries
    ):
        raise ValueError("ACQUIRED_SOURCE_MEMBERSHIP_CHANGED")
    cache = sec.Cache(OUTPUT)
    for row in inventory["sources"]:
        raw = cache.read(row["sha256"])
        entry = entries[row["accession"]]
        if (
            row["url"] != entry["source_url"]
            or row["year"] != entry["year"]
            or row["role"] != entry["role"]
            or row["object_locator"]
            != str(cache.object_path(row["sha256"]).relative_to(OUTPUT)).replace("\\", "/")
            or len(raw) != row["bytes"]
            or check_submission(raw, entry) != row["container_validation"]
        ):
            raise ValueError("ACQUIRED_SOURCE_REPLAY_MISMATCH")
    byte_state = json.loads((OUTPUT / "byte-budget.json").read_bytes())
    request_state = json.loads((OUTPUT / "request-budget.json").read_bytes())
    if (
        byte_state["plan_sha256"] != SOURCE_SHA
        or byte_state["limit"] != BYTE_CAP
        or not sum(x["bytes"] for x in inventory["sources"])
        <= byte_state["received_body_bytes"]
        <= BYTE_CAP
        or byte_state["received_body_bytes"] != inventory["received_body_bytes"]
        or request_state["requests"] != inventory["HTTP_requests"]
        or request_state["request_cap"] != REQUEST_CAP
        or not 0 <= request_state["requests"] <= REQUEST_CAP
        or inventory["old_discovery_budgets"] != old_budget_hashes()
        or inventory["code_registry"] != proof["acquisition_registry"]
    ):
        raise ValueError("ACQUISITION_BUDGET_OR_CODE_REPLAY_MISMATCH")
    result = {
        "status": "ALL_100_RAW_SUBMISSIONS_REPLAY_PASSED",
        "source_manifest_sha256": SOURCE_SHA,
        "acquired_manifest_sha256": sec.file_sha(path),
        "source_count": 100,
        "cached_body_bytes": sum(x["bytes"] for x in inventory["sources"]),
        "received_body_bytes": byte_state["received_body_bytes"],
        "HTTP_requests": request_state["requests"],
        "event_extraction_started": False,
        "original_718_sample_changes": 0,
        "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
    }
    discovery.m.immutable(OUTPUT / "acquisition-audit.json", result)
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("command", choices=["acquire", "audit"])
    command = cli.parse_args().command
    try:
        result = acquire() if command == "acquire" else audit()
        print(
            json.dumps({k: v for k, v in result.items() if k not in {"sources", "code_registry"}}),
            flush=True,
        )
    except (
        ValueError,
        KeyError,
        OSError,
        RuntimeError,
        sec.AcquisitionError,
        http.client.HTTPException,
    ) as error:
        reason = (
            str(error)
            if isinstance(error, (ValueError, sec.AcquisitionError))
            else type(error).__name__
        )
        if OUTPUT.exists():
            sec.append_record(
                OUTPUT / "stops.jsonl", {"status": "STOPPED_WITHOUT_COMPLETION", "reason": reason}
            )
        print(
            json.dumps({"status": "ACQUISITION_STOPPED_NO_GATE_OPENED", "reason": reason}),
            flush=True,
        )
        raise SystemExit(2) from None
