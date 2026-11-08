"""Synthetic bounded-client, frozen-ledger, review and workflow-boundary checks."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "tools/corporate_events/parser_qa.py"
spec = importlib.util.spec_from_file_location("parser_qa_test", ENTRY)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
URL = "https://www.sec.gov/Archives/edgar/data/123/0000000123-24-000001.txt"
AGENT = "SyntheticResearch (Researcher <researcher@valid.test>)"
ACC = "0000000123-24-000001"


class Clock:
    def __init__(self) -> None:
        self.value = 1000.0
        self.delays: list[float] = []

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.value += seconds


class Fake:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    def get(self, url: str, agent: str) -> Any:
        self.calls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def submission() -> bytes:
    return (
        "<SEC-DOCUMENT>"
        f"{ACC}"
        ".txt\n<SEC-HEADER>\nACCESSION NUMBER: "
        f"{ACC}"
        "\n<ACCEPTANCE-DATETIME>20241104163000\n<DOCUMENT>\n"
        "<TYPE>8-K\n<SEQUENCE>1\n<FILENAME>body.htm\n<TEXT>\n"
        "The board approved a new stock repurchase progra"
        "m of up to $100 million USD in open market trans"
        "actions.\n</TEXT>\n</DOCUMENT>\n"
    ).encode()


def response(status: int = 200, **headers: str) -> Any:
    return m.sec.Response(
        status, {"content-type": "text/plain", **headers}, submission() if status == 200 else b""
    )


def setup_client(tmp_path: Path, transport: Any) -> tuple[Clock, Any, Any, Path]:
    clock = Clock()
    root = tmp_path / "shared"
    root.mkdir()
    cache_root = root / "parser-qa"
    cache_root.mkdir()
    snapshot = cache_root / "runs" / "synthetic"
    snapshot.mkdir(parents=True)
    scheduler = m.sec.Scheduler(root, clock.now, clock.sleep)
    client = m.documents.DocumentClient(
        m.sec, cache_root, snapshot, scheduler, transport, {URL}, AGENT, clock.now() + 100
    )
    return clock, scheduler, client, snapshot


@pytest.mark.parametrize(
    "url",
    [
        "https://finance.yahoo.com/quote/EXAMPLE/",
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000123.json",
        "https://www.sec.gov/files/company_tickers.json",
        "https://www.sec.gov/Archives/edgar/data/124/0000000123-24-000001.txt",
        URL + "?extra=1",
        URL.replace("https:", "http:"),
    ],
)
def test_body_urls_are_exactly_pilot_registered(url: str) -> None:
    with pytest.raises(ValueError):
        m.documents.validate_url(url, {URL})


def test_shared_scheduler_cache_and_private_header_redaction(tmp_path: Path) -> None:
    transport = Fake(response())
    _, scheduler, client, snapshot = setup_client(tmp_path, transport)
    with scheduler:
        raw, record = client.fetch(URL)
        client.deadline = -1
        assert client.fetch(URL) == (raw, record)
    assert len(transport.calls) == 1
    ledger = (snapshot / "requests.jsonl").read_text()
    assert "researcher@valid.test" not in ledger
    entries = [json.loads(line) for line in ledger.splitlines()]
    assert entries[0]["headers_hash"] and entries[1]["cache_hit"]
    with (
        m.sec.Scheduler(scheduler.root) as owner,
        pytest.raises(m.sec.AcquisitionError, match="OWNER"),
        m.sec.Scheduler(owner.root),
    ):
        pass


def test_403_persists_shared_block_and_local_guard_never_retries(tmp_path: Path) -> None:
    transport = Fake(response(403), response())
    _, scheduler, client, _ = setup_client(tmp_path, transport)
    with scheduler:
        with pytest.raises(m.sec.AcquisitionError, match="403"):
            client.fetch(URL)
        with pytest.raises(m.sec.AcquisitionError, match="BLOCKED"):
            client.fetch(URL)
    assert len(transport.calls) == 1


def test_repeated_429_stops_and_retry_after_is_honored(tmp_path: Path) -> None:
    transport = Fake(response(429, **{"retry-after": "3"}), response(429))
    clock, scheduler, client, _ = setup_client(tmp_path, transport)
    with scheduler, pytest.raises(m.sec.AcquisitionError, match="CONSECUTIVE_429"):
        client.fetch(URL)
    assert len(transport.calls) == 2 and sum(clock.delays) >= 3


def test_cross_document_redirect_rejected_without_following(tmp_path: Path) -> None:
    other = URL.replace("000001.txt", "000002.txt")
    transport = Fake(response(302, location=other), response())
    _, scheduler, client, _ = setup_client(tmp_path, transport)
    client.allowed.add(other)
    with scheduler, pytest.raises(m.sec.AcquisitionError, match="IDENTITY"):
        client.fetch(URL)
    assert len(transport.calls) == 1


def test_total_byte_budget_persists_and_is_not_reset_by_new_run(tmp_path: Path) -> None:
    path = tmp_path / "budget.json"
    budget = m.documents.ByteBudget(path, 100, "a" * 64)
    assert budget.cap() == 99
    budget.charge(70)
    continued = m.documents.ByteBudget(path, 100, "a" * 64)
    assert continued.cap() == 29 and continued.spent == 70
    with pytest.raises(ValueError, match="IDENTITY"):
        m.documents.ByteBudget(path, 200, "a" * 64)
    with pytest.raises(ValueError, match="INVARIANT"):
        continued.charge(31)


def test_error_body_not_read_and_actual_received_bytes_charged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Reply:
        status = 403

        def getheaders(self) -> list[tuple[str, str]]:
            return [("content-type", "text/html")]

        def read(self, count: int) -> bytes:
            raise AssertionError("error body must not be read")

    class Connection:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def request(self, *args: Any, **kwargs: Any) -> None:
            pass

        def getresponse(self) -> Reply:
            return Reply()

        def close(self) -> None:
            pass

    monkeypatch.setattr(m.documents.http.client, "HTTPSConnection", Connection)
    budget = m.documents.ByteBudget(tmp_path / "budget.json", 1000, "a" * 64)
    context = m.ssl.SSLContext(m.ssl.PROTOCOL_TLS_CLIENT)
    transport = m.documents.HttpsDocuments(m.sec, {URL}, context, budget)
    assert transport.get(URL, AGENT).status == 403 and budget.spent == 0


def test_frozen_schema_pilot_store_and_review_packets_never_certify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pilot = tmp_path / "pilot"
    pilot.mkdir()
    monkeypatch.setattr(m, "PILOT", pilot)
    raw = submission()
    m.sec.Cache(pilot).store(URL, raw, "text/plain")
    entry = {
        "url": URL,
        "form": "8-K",
        "canonical_form": "8-K",
        "era": "2023-2024",
        "cik_raw": "123",
        "filing_date": "2024-11-04",
        "accession": ACC,
    }
    run = pilot / "runs" / "parse1"
    run.mkdir(parents=True)
    result = m.parse_pilot(run, {"entries": [entry]}, "a" * 64, {})
    assert result["documents"] == 1 and result["parser_candidates"] >= 1
    with sqlite3.connect(run / "development-events.sqlite3") as db:
        assert (
            db.execute("SELECT count(*) FROM events WHERE disposition='QUALIFYING'").fetchone()[0]
            == 0
        )
        assert (
            db.execute(
                "SELECT count(*) FROM timing_authority WHERE registered_public_datetime IS NOT NULL"
            ).fetchone()[0]
            == 0
        )
        assert db.execute("SELECT DISTINCT split_label FROM events").fetchall() == [
            ("UNRESOLVED_DATE",)
        ]
        assert (
            db.execute(
                "SELECT count(*) FROM event_security_mapping_plac"
                "eholders WHERE target_identifier IS NOT NULL"
            ).fetchone()[0]
            == 0
        )
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            db.execute("DELETE FROM events")
    db.close()
    report = json.loads((run / "parser-development-report.json").read_bytes())
    assert (
        report["manual_precision"] == "NOT_MEASURED" and report["qualifying_events_certified"] == 0
    )
    assert "CLOSED" in report["full_extraction_gate"]
    packets = list((run / "packets").glob("*.json"))
    packet = json.loads(packets[0].read_bytes())
    assert packet["public_cutoff_utc"] is None and packet["later_documents_included"] is False
    html = packets[0].with_suffix(".html").read_text(encoding="utf-8")
    assert "default-src 'none'" in html
    control = packets[0].with_suffix(".control.html").read_text(encoding="utf-8")
    assert "NEW_AUTHORIZATION" not in control and "Independent source control" in control
    assert (run / "review-index.html").is_file()
    candidate = packet["candidate_proposals"][0]
    review = {
        "source_id": packet["source_id"],
        "event_id": candidate["event_id"],
        "reviewer_id": "ActualSyntheticReviewer",
        "decision": "AMBIGUOUS",
        "reason": "Synthetic test label only",
        "evidence_ids": [candidate["evidence"]["evidence_id"]],
        "supersedes_review_id": None,
        "scope": "DEVELOPMENT_FACT_REVIEW_ONLY",
        "family": "SHARE_REPURCHASE",
        "role": "PARSER_CANDIDATE",
        "correct_state": "NEW_AUTHORIZATION",
        "event_presence": True,
    }
    (run / "review-inbox").mkdir()
    (run / "review-inbox" / "synthetic.json").write_text(json.dumps(review))
    appended = m.record_review("parse1", "synthetic.json")
    assert appended["formal_historical_classification_review"] is False
    assert len((run / "fact-review-history.jsonl").read_text().splitlines()) == 1


@pytest.mark.parametrize(
    "probe",
    [
        "__import__('yfinance')",
        "__import__('wrds')",
        "socket.create_connection(('query1.finance.yahoo.com',443))",
        "Path('C:/outside/prohibited-input.csv').read_bytes()",
        "subprocess.run([sys.executable,'-c','pass'])",
        (
            "sqlite3.connect(':memory:').execute(\"ATTACH DATA"
            "BASE 'C:/outside/vendor.db' AS external\")"
        ),
    ],
)
def test_isolated_parser_workflow_denies_provider_file_process_and_database_escapes(
    probe: str,
) -> None:
    code = (
        "import runpy,sys,socket,subprocess,sqlite3;from pathlib import Path;"
        + f"m=runpy.run_path({str(ENTRY)!r},run_name='probe');m['install']();"
        + probe
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code], capture_output=True, text=True, check=False
    )
    assert completed.returncode != 0
    assert any(word in completed.stderr for word in ("DENIED", "not authorized", "unable to open"))


def test_second_parser_iteration_reuses_identical_development_qa_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pilot = tmp_path / "pilot"
    pilot.mkdir()
    monkeypatch.setattr(m, "PILOT", pilot)
    m.sec.Cache(pilot).store(URL, submission(), "text/plain")
    entry = {
        "url": URL,
        "form": "8-K",
        "canonical_form": "8-K",
        "era": "2023-2024",
        "cik_raw": "123",
        "filing_date": "2024-11-04",
        "accession": ACC,
    }
    first = pilot / "runs" / "first"
    first.mkdir(parents=True)
    m.parse_pilot(first, {"entries": [entry]}, "a" * 64, {})
    original = (pilot / "frozen-development-qa-selection.json").read_bytes()
    second = pilot / "runs" / "second"
    second.mkdir()
    m.parse_pilot(second, {"entries": [entry]}, "a" * 64, {})
    assert (pilot / "frozen-development-qa-selection.json").read_bytes() == original
    assert (second / "development-qa-selection.json").read_bytes() == (
        first / "development-qa-selection.json"
    ).read_bytes()


def test_local_firewall_denial_stops_before_retrying_another_source(tmp_path: Path) -> None:
    transport = Fake(m.sec.FirewallError("METADATA_FILE_ACCESS_DENIED"), response())
    _, scheduler, client, _ = setup_client(tmp_path, transport)
    with scheduler, pytest.raises(m.sec.AcquisitionError, match="LOCAL_FIREWALL_DENIAL"):
        client.fetch(URL)
    assert len(transport.calls) == 1


def test_five_attempt_budget_persists_for_document_client(tmp_path: Path) -> None:
    transport = Fake(*(response(503) for _ in range(5)))
    _, scheduler, client, _ = setup_client(tmp_path, transport)
    with scheduler:
        with pytest.raises(m.sec.AcquisitionError, match="FIVE_ATTEMPTS"):
            client.fetch(URL)
        with pytest.raises(m.sec.AcquisitionError, match="FIVE_ATTEMPTS"):
            client.fetch(URL)
    assert len(transport.calls) == 5


def test_partial_http_body_is_charged_before_transport_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Reply:
        status = 200

        def __init__(self) -> None:
            self.calls = 0

        def getheaders(self) -> list[tuple[str, str]]:
            return [("content-type", "text/plain")]

        def read(self, count: int) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return b"known received bytes"
            raise m.documents.http.client.IncompleteRead(b"partial", 100)

    class Connection:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def request(self, *args: Any, **kwargs: Any) -> None:
            pass

        def getresponse(self) -> Reply:
            return Reply()

        def close(self) -> None:
            pass

    monkeypatch.setattr(m.documents.http.client, "HTTPSConnection", Connection)
    budget = m.documents.ByteBudget(tmp_path / "budget.json", 1000, "a" * 64)
    transport = m.documents.HttpsDocuments(
        m.sec, {URL}, m.ssl.SSLContext(m.ssl.PROTOCOL_TLS_CLIENT), budget
    )
    with pytest.raises(m.documents.http.client.IncompleteRead):
        transport.get(URL, AGENT)
    assert budget.spent == len(b"known received bytespartial")
    assert m.documents.ByteBudget(tmp_path / "budget.json", 1000, "a" * 64).spent == budget.spent


@pytest.mark.parametrize("issuer_present", [True, False])
def test_joint_owner_statement_duplicates_do_not_multiply_events_or_infer_issuer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, issuer_present: bool
) -> None:
    pilot = tmp_path / "pilot"
    pilot.mkdir()
    monkeypatch.setattr(m, "PILOT", pilot)
    owner = (
        "<reportingOwner><reportingOwnerId><rptOwnerCik>4"
        "56</rptOwnerCik><rptOwnerName>Joint Owner</rptOw"
        "nerName></reportingOwnerId></reportingOwner>"
    )
    issuer = "<issuer><issuerCik>123</issuerCik></issuer>" if issuer_present else ""
    xml = (
        "<ownershipDocument>"
        + issuer
        + owner
        + owner
        + "<nonDerivativeTable><nonDerivativeTransaction><s"
        "ecurityTitle><value>Common Stock</value></securi"
        "tyTitle><transactionCoding><transactionCode>P</t"
        "ransactionCode></transactionCoding><transactionA"
        "mounts><transactionShares><value>7</value></tran"
        "sactionShares><transactionAcquiredDisposedCode><"
        "value>A</value></transactionAcquiredDisposedCode"
        "></transactionAmounts></nonDerivativeTransaction"
        "></nonDerivativeTable></ownershipDocument>"
    )
    raw = submission().replace(b"<TYPE>8-K", b"<TYPE>4")
    start, end = raw.index(b"<TEXT>"), raw.index(b"</TEXT>")
    raw = raw[:start] + b"<TEXT>\n<XML>" + xml.encode() + b"</XML>\n" + raw[end:]
    m.sec.Cache(pilot).store(URL, raw, "text/plain")
    entry = {
        "url": URL,
        "form": "4",
        "canonical_form": "4",
        "era": "2023-2024",
        "cik_raw": "999",
        "filing_date": "2024-11-04",
        "accession": ACC,
    }
    run = pilot / "runs" / "joint"
    run.mkdir(parents=True)
    m.parse_pilot(run, {"entries": [entry]}, "a" * 64, {})
    with sqlite3.connect(run / "development-events.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM event_reporting_owners").fetchone()[0] == 1
        claims = json.loads(
            db.execute("SELECT relation_from_sec FROM event_reporting_owners").fetchone()[0]
        )
        assert len(claims) == 2
        expected = ("RESOLVED_SEC", "0000000123") if issuer_present else ("UNRESOLVED", None)
        assert (
            db.execute("SELECT identity_status,cik_normalized FROM issuer_identities").fetchone()
            == expected
        )
    db.close()
