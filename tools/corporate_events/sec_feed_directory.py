"""Bounded SEC Feed directory discovery; never follow listed files automatically."""

from __future__ import annotations

import argparse
import http.client
import json
import re
import ssl
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# Reuse the frozen scheduler, firewall, immutable cache and durable reservation machinery.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sec_timing_diagnostics as base  # noqa: E402

sec = base.sec
ROOT, PRIVATE, PROTOCOL = base.ROOT, base.PRIVATE, base.PROTOCOL
OUTPUT = PRIVATE / "repurchase-feed-directory-v1"
CAP, TOTAL = 524_288, 2_097_152
URLS = [
    f"https://www.sec.gov/Archives/edgar/Feed/{year}/QTR{quarter}/index.json"
    for year, quarter in [(2007, 3), (2008, 3), (2010, 3), (2011, 4)]
]
REGISTERED = {
    "tools/corporate_events/sec_feed_directory.py",
    "tests/test_sec_feed_directory.py",
    "protocols/corporate-event-alpha-v1/sec-feed-directory-policy-v1.json",
}


def parse_listing(body: bytes, url: str) -> dict[str, Any]:
    """Classify names without granting timing authority or creating follow-up requests."""
    if url not in URLS or len(body) > CAP:
        raise ValueError("DIRECTORY_INPUT_OUTSIDE_SCOPE")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("DIRECTORY_DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    data = json.loads(body.decode("utf-8"), object_pairs_hook=unique)
    if not isinstance(data, dict) or not isinstance(data.get("directory"), dict):
        raise ValueError("DIRECTORY_SCHEMA_INVALID")
    directory = data["directory"]
    expected = urlsplit(url).path.removesuffix("/index.json")
    if directory.get("name", "").rstrip("/") != expected:
        raise ValueError("DIRECTORY_PATH_MISMATCH")
    items = directory.get("item")
    if not isinstance(items, list) or len(items) > 4096:
        raise ValueError("DIRECTORY_ITEM_LIST_INVALID")
    observed, names = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("DIRECTORY_ITEM_INVALID")
        name = item.get("name")
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", name)
            or ".." in name
            or name in names
        ):
            raise ValueError("DIRECTORY_UNSAFE_OR_DUPLICATE_NAME")
        names.add(name)
        if re.fullmatch(r"dissem2_[0-9]{2}-[0-9]{2}-[0-9]{4}\.EOD", name):
            category = "POSSIBLE_DAILY_AUDIT_REQUIRES_SEPARATE_VALIDATION"
        elif name.endswith((".nc.tar.gz", ".tar.gz", ".zip")):
            category = "ARCHIVE_NO_AUTOMATIC_DOWNLOAD"
        elif name.lower().endswith((".idx", ".json", ".xml", ".csv", ".txt", ".manifest")):
            category = "POSSIBLE_METADATA_REQUIRES_SEPARATE_VALIDATION"
        else:
            category = "OTHER_LISTED_NAME"
        observed.append({"name": name, "category": category})
    return {
        "url": url,
        "listing_items": len(observed),
        "observations": sorted(observed, key=lambda item: item["name"]),
        "automatic_followup_requests": 0,
        "accession_membership_proven": False,
        "public_times_certified": 0,
        "directory_dates_used_as_public_time": False,
    }


class Ledger(base.Ledger):
    def __init__(self, root: Path, binding: str, urls: list[str]) -> None:
        if urls != URLS:
            raise ValueError("DIRECTORY_EXACT_URL_SET_REQUIRED")
        super().__init__(root, binding, urls)
        if self.state["received_body_bytes"] > TOTAL or any(
            a["received_body_bytes"] > CAP for a in self.state["attempts"]
        ):
            raise ValueError("DIRECTORY_EXISTING_BYTE_CAP_VIOLATION")

    def charge(self, count: int) -> None:
        super().charge(count)
        if (
            self.state["received_body_bytes"] > TOTAL
            or self.state["attempts"][-1]["received_body_bytes"] > CAP
        ):
            self.stop("DIRECTORY_BYTE_CAP_VIOLATION")
            raise ValueError("DIRECTORY_BYTE_CAP_VIOLATION")


def checked_headers(status: int, pairs: list[tuple[str, str]]) -> int:
    headers = {key.lower(): value for key, value in pairs}
    critical = {"content-length", "transfer-encoding", "content-encoding", "content-type"}
    names = [key.lower() for key, _ in pairs if key.lower() in critical]
    if len(names) != len(set(names)):
        raise ValueError("DIRECTORY_DUPLICATE_FRAMING_HEADERS")
    if status != 200:
        raise ValueError(f"DIRECTORY_HTTP_{status}_STOP_NO_FALLBACK")
    length = headers.get("content-length", "")
    if not re.fullmatch(r"[0-9]{1,6}", length) or not 0 < int(length) <= CAP:
        raise ValueError("DIRECTORY_CONTENT_LENGTH_INVALID_OR_EXCEEDS_CAP")
    if (
        headers.get("transfer-encoding")
        or headers.get("content-encoding", "identity").lower() != "identity"
        or headers.get("content-range")
    ):
        raise ValueError("DIRECTORY_REPRESENTATION_NOT_COMPLETE_IDENTITY")
    if headers.get("content-type", "").split(";", 1)[0].strip().lower() not in {
        "application/json", "text/json",
    }:
        raise ValueError("DIRECTORY_JSON_CONTENT_TYPE_REQUIRED")
    return int(length)


class Transport:
    def __init__(self, context: ssl.SSLContext, factory: Any = http.client.HTTPSConnection):
        self.context, self.factory = context, factory
        self.body = b""

    def get(self, url: str, agent: str, ledger: Ledger) -> bytes:
        self.body = b""
        if (
            url not in URLS
            or not ledger.state["attempts"]
            or ledger.state["attempts"][-1]["url"] != url
            or ledger.state["attempts"][-1]["outcome"] != "RESERVED"
            or ledger.state["attempts"][-1]["http_status"] is not None
        ):
            raise ValueError("DIRECTORY_UNRESERVED_OR_UNAPPROVED_URL")
        if not self.context.check_hostname or self.context.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError("DIRECTORY_TLS_VERIFICATION_REQUIRED")
        token = sec.NETWORK_HOST.set("www.sec.gov")
        connection = None
        try:
            connection = self.factory("www.sec.gov", 443, timeout=30, context=self.context)
            connection.request("GET", urlsplit(url).path, headers={
                "User-Agent": agent, "Accept": "application/json",
                "Accept-Encoding": "identity", "Connection": "close",
            })
            reply = connection.getresponse()
            pairs = reply.getheaders()
            ledger.response(reply.status, {k.lower(): v for k, v in pairs})
            remaining = checked_headers(reply.status, pairs)
            while remaining:
                count = min(65536, remaining)
                try:
                    chunk = reply.read(count)
                except http.client.IncompleteRead as error:
                    self.body += error.partial
                    ledger.charge(len(error.partial))
                    raise ValueError("DIRECTORY_PARTIAL_RESPONSE_STOP") from None
                self.body += chunk
                ledger.charge(len(chunk))
                if not chunk or len(chunk) > count:
                    raise ValueError("DIRECTORY_RESPONSE_READ_BOUND_FAILURE")
                remaining -= len(chunk)
            if not reply.isclosed():
                raise ValueError("DIRECTORY_RESPONSE_END_UNVERIFIED")
            return self.body
        finally:
            if connection is not None:
                connection.close()
            sec.NETWORK_HOST.reset(token)


def preflight() -> tuple[dict[str, Any], str]:
    sec.preflight()
    registry = json.loads((PROTOCOL / "sec-feed-directory-extension-v1.json").read_bytes())
    if set(registry["files"]) != REGISTERED:
        raise ValueError("DIRECTORY_REGISTRY_SCOPE_INVALID")
    for name, digest in registry["files"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("DIRECTORY_REGISTERED_FILE_CHANGED")
    policy = json.loads((PROTOCOL / "sec-feed-directory-policy-v1.json").read_bytes())
    if policy["urls"] != URLS or policy["per_response_cap"] != CAP or policy["total_cap"] != TOTAL:
        raise ValueError("DIRECTORY_POLICY_SCOPE_CHANGED")
    for name, digest in policy["preserved_private_files"].items():
        if sec.file_sha(PRIVATE / name) != digest:
            raise ValueError("DIRECTORY_PRIOR_RECORD_CHANGED")
    for name, digest in policy["frozen_dependencies"].items():
        if sec.file_sha(ROOT / name) != digest:
            raise ValueError("DIRECTORY_FROZEN_DEPENDENCY_CHANGED")
    verify_prior_attempts(policy)
    return policy, sec.sha(base.canonical(registry))


def verify_prior_attempts(policy: dict[str, Any]) -> None:
    prior = json.loads((PRIVATE / policy["attempt_snapshot"]).read_bytes())
    current = json.loads((PRIVATE / "scheduler-state.json").read_bytes())["attempts_by_url"]
    if (
        any(current.get(url) != count for url, count in prior.items())
        or set(current) - set(prior) - set(URLS)
        or any(current.get(url, 0) not in {0, 1} for url in URLS)
    ):
        raise ValueError("DIRECTORY_PRIOR_ATTEMPTS_CHANGED")


def authorized(binding: str) -> dict[str, Any]:
    path = PRIVATE / "feed-directory-authorization-v1.json"
    if not path.exists():
        raise ValueError("DIRECTORY_FRESH_EXPLICIT_AUTHORIZATION_REQUIRED")
    approval: dict[str, Any] = json.loads(path.read_bytes())
    if (
        approval.get("approved") is not True
        or approval.get("registry_binding") != binding
        or approval.get("scope") != "FOUR_FEED_DIRECTORY_JSON_REQUESTS_ONLY"
        or not approval.get("verbatim_user_approval")
    ):
        raise ValueError("DIRECTORY_FRESH_EXPLICIT_AUTHORIZATION_REQUIRED")
    return approval


def audit(ledger: Ledger) -> dict[str, Any]:
    listings = []
    for attempt in ledger.state["attempts"]:
        if attempt["body_sha256"]:
            body = sec.Cache(ledger.root).read(attempt["body_sha256"])
            if len(body) != attempt["received_body_bytes"]:
                raise ValueError("DIRECTORY_CACHE_BYTE_COUNT_MISMATCH")
            if attempt["outcome"] == "DIRECTORY_PARSED":
                listings.append(parse_listing(body, attempt["url"]))
    return {
        "status": ledger.state["status"], "stop_reason": ledger.state["stop_reason"],
        "attempts": len(ledger.state["attempts"]),
        "received_body_bytes": ledger.state["received_body_bytes"], "listings": listings,
        "public_times_certified": 0, "automatic_followup_requests": 0,
        "prior_budgets_reset": 0, "original_718_sample_changes": 0,
        "filing_bodies_downloaded": 0, "regressions_run": 0,
        "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
    }


def run(command: str) -> dict[str, Any]:
    policy, binding = preflight()
    if command == "validate":
        contact = sec.private_contact(ROOT)
        del contact
        return {
            "status": "OFFLINE_READY_AWAITING_FRESH_AUTHORIZATION", "network_requests": 0,
            "contact_configured": True, "request_cap": 4, "byte_cap": TOTAL,
            "live_routes_verified": False, "registry_binding": binding,
            "firewall_counters": dict.fromkeys(sec.FIREWALL_COUNTERS, 0),
        }
    approval = authorized(binding)
    ledger_binding = sec.sha(base.canonical({"registry": binding, "approval": approval}))
    agent, context = None, None
    if command == "acquire":
        agent = "CorporateEventAlphaResearch/feed-directory-v1 (" + sec.private_contact(ROOT) + ")"
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    sec.EXTENSION_FILES = (*sec.EXTENSION_FILES, *REGISTERED, *policy["frozen_dependencies"])
    sec.install_firewall()
    if command == "audit":
        sys.addaudithook(lambda event, args: (
            (_ for _ in ()).throw(RuntimeError("DIRECTORY_AUDIT_OFFLINE_ONLY"))
            if event.startswith(("socket.", "http.client.")) else None
        ))
        if not (OUTPUT / "state.json").exists():
            raise ValueError("DIRECTORY_AUDIT_REQUIRES_EXISTING_STATE")
        ledger = Ledger(OUTPUT, ledger_binding, URLS)
    else:
        assert agent and context
        with sec.Scheduler(PRIVATE) as scheduler:
            ledger = Ledger(OUTPUT, ledger_binding, URLS)
            if ledger.state["status"] == "RUNNING":
                ledger.stop("DIRECTORY_INTERRUPTED_NO_AUTOMATIC_RESUME")
            if ledger.state["status"] == "READY":
                transport = Transport(context)
                deadline = time.time() + 180
                for url in URLS:
                    try:
                        if scheduler.attempts_used(url):
                            raise ValueError("DIRECTORY_GLOBAL_ATTEMPT_ALREADY_SPENT")
                        scheduler.start(deadline)
                        ledger.reserve(url)
                        scheduler.record_attempt(url)
                    except Exception as error:
                        ledger.stop(base.safe_reason(error))
                        break
                    reason = None
                    try:
                        parse_listing(transport.get(url, agent, ledger), url)
                    except Exception as error:
                        reason = base.safe_reason(error)
                    ledger.finish(transport.body, reason or "DIRECTORY_PARSED", [])
                    if reason:
                        status = ledger.state["attempts"][-1]["http_status"]
                        if status == 429:
                            retry = ledger.state["attempts"][-1]["response_headers"].get(
                                "retry-after"
                            )
                            scheduler.hold(sec.retry_after_seconds(retry, sec.utc_now()), reason)
                        if status in {403, 429}:
                            scheduler.block(reason)
                        ledger.stop(reason)
                        break
                else:
                    ledger.state["status"] = "COMPLETED_DIRECTORY_DISCOVERY_ONLY"
                    ledger.save()
    verify_prior_attempts(policy)
    summary = audit(ledger)
    base.immutable(OUTPUT / "summary.json", summary)
    base.immutable(OUTPUT / "completion-receipt.json", {
        "summary_sha256": sec.file_sha(OUTPUT / "summary.json"),
        "state_sha256": sec.file_sha(OUTPUT / "state.json"), "binding": ledger_binding,
    })
    return summary


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("command", choices=["validate", "acquire", "audit"])
    try:
        print(json.dumps(run(cli.parse_args().command)), flush=True)
    except Exception as error:
        print(json.dumps({"status": "DIRECTORY_STOPPED", "reason": base.safe_reason(error)}))
        raise SystemExit(2) from None
