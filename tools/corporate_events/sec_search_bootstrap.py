"""Cache the official search UI and its backend-routing script; never execute it."""

from __future__ import annotations

import importlib.util
import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "reports/corporate-event-alpha-v1/phase2-private"
PROTOCOL = ROOT / "protocols/corporate-event-alpha-v1"
OUTPUT = PRIVATE / "repurchase-discovery-v1"
REGISTERED = (
    "tools/corporate_events/sec_search_metadata_client.py",
    "tools/corporate_events/sec_search_bootstrap.py",
    "tests/test_sec_search_metadata_client.py",
    "tests/test_sec_search_bootstrap.py",
    "protocols/corporate-event-alpha-v1/sec-search-discovery-policy-v1.json",
)


def load(name: str, path: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sec = load("search_bootstrap_sec", "tools/corporate_events/sec_census.py")
client = load("search_bootstrap_transport", "tools/corporate_events/sec_search_metadata_client.py")


class Scripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        value = dict(attrs).get("src")
        if value:
            self.urls.append(urljoin(client.PAGE, value))


def script_urls(raw: bytes) -> list[str]:
    parser = Scripts()
    parser.feed(raw.decode("utf-8"))
    accepted = []
    for url in parser.urls:
        try:
            client.validate_request(url, "GET", None)
        except ValueError:
            continue
        if url != client.PAGE:
            accepted.append(url)
    return sorted(set(accepted))


def run() -> dict[str, Any]:
    proof = sec.preflight()
    ext = json.loads((PROTOCOL / "sec-search-discovery-extension-v1.json").read_bytes())
    if set(ext["files"]) != set(REGISTERED):
        raise ValueError("SEARCH_REGISTRY_SCOPE_INVALID")
    for name, expected in ext["files"].items():
        if sec.file_sha(ROOT / name) != expected:
            raise ValueError("SEARCH_CODE_HASH_MISMATCH")
    agent = sec.private_contact(ROOT)
    OUTPUT.mkdir(exist_ok=True)
    if not (OUTPUT / "bootstrap-preflight.json").exists():
        sec.write_new(
            OUTPUT / "bootstrap-preflight.json",
            sec.json_bytes(
                {
                    "protocol": proof,
                    "contact_status": "VALID_PRIVATE_CONTACT",
                    "registry": ext,
                    "filing_body_downloads_allowed": False,
                    "market_data_allowed": False,
                }
            ),
        )
    sec.EXTENSION_FILES = (*sec.EXTENSION_FILES, *REGISTERED)
    sec.install_firewall()
    with sec.Scheduler(PRIVATE) as scheduler:
        transport = client.Client(sec, OUTPUT, scheduler, agent, "diagnostic", 12, 33554432)
        page = transport.fetch(client.PAGE)
        urls = script_urls(page)
        # Search-app script names only. External libraries and arbitrary URLs are excluded.
        preferred = [
            u
            for u in urls
            if any(token in u.rsplit("/", 1)[-1].lower() for token in ("efts", "search", "edgar"))
        ]
        result: dict[str, Any] = {
            "status": "SCRIPT_SELECTION_REQUIRED",
            "scripts": urls,
            "preferred_scripts": preferred,
        }
        for url in preferred[:2]:
            raw = transport.fetch(url)
            if b"efts.sec.gov" in raw or b"search-index" in raw:
                result.update(
                    status="OFFICIAL_SEARCH_ROUTING_SCRIPT_CACHED",
                    routing_script_url=url,
                    routing_script_sha256=sec.sha(raw),
                    official_page_url=client.PAGE,
                    official_page_sha256=sec.sha(page),
                    documented_public_API_contract_claimed=False,
                )
                break
    result.update(
        filing_body_downloads=0,
        market_data_queries=0,
        diagnostic_requests_total=transport.state["requests"],
        diagnostic_received_bytes=transport.state["bytes"],
    )
    sec.append_record(OUTPUT / "bootstrap-results.jsonl", result)
    print(json.dumps(result), flush=True)
    return result


if __name__ == "__main__":
    run()
