"""Synthetic tests only: transport, cache, metadata parsing and workflow capability."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sqlite3
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/corporate_events/sec_census.py"
spec = importlib.util.spec_from_file_location("sec_metadata_census", SCRIPT)
assert spec and spec.loader
module: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
URL = "https://www.sec.gov/Archives/edgar/full-index/2006/QTR1/master.zip"
AGENT = "SyntheticResearch (Researcher <researcher@valid.test>)"


def raw_index(year: int = 2006, quarter: int = 1, duplicate: bool = False) -> bytes:
    lines = [
        "Description: synthetic master index",
        "CIK|Company Name|Form Type|Date Filed|Filename",
        "----------------",
    ]
    for index, form in enumerate(("4", "SC 13D", "8-K"), 1):
        lines.append(
            f"123|Synthetic Issuer {index}|{form}|{year}-{quarter * 3:02d}-01|"
            f"edgar/data/123/0000000123-{year % 100:02d}-{quarter * 10 + index:06d}.txt"
        )
    if duplicate:
        lines.append(lines[-1])
    return ("\n".join(lines) + "\n").encode()


def zipped(raw: bytes, name: str = "master.idx") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, raw)
    return output.getvalue()


def response(status: int = 200, **headers: str) -> Any:
    return module.Response(
        status, {"content-type": "application/zip", **headers}, zipped(raw_index())
    )


class Clock:
    def __init__(self) -> None:
        self.value = 1000.0
        self.delays: list[float] = []

    def now(self) -> float:
        return self.value

    def sleep(self, delay: float) -> None:
        self.delays.append(delay)
        self.value += delay


class FakeTransport:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def get(self, url: str, agent: str) -> Any:
        self.calls.append((url, agent))
        answer = self.responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def setup(tmp_path: Path) -> tuple[Clock, Any, Path]:
    clock = Clock()
    scheduler = module.Scheduler(tmp_path, clock.now, clock.sleep)
    snapshot = tmp_path / "snapshots" / "synthetic"
    snapshot.mkdir(parents=True)
    return clock, scheduler, snapshot


def client(tmp_path: Path, snapshot: Path, scheduler: Any, transport: Any, **kwargs: Any) -> Any:
    return module.Client(
        tmp_path, snapshot, AGENT, scheduler, transport, jitter=lambda: 0.0, **kwargs
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://www.sec.gov/Archives/edgar/full-index/2006/QTR1/master.zip",
        "https://www.sec.gov.evil.test/Archives/edgar/full-index/2006/QTR1/master.zip",
        "https://finance.yahoo.com/",
        "https://www.sec.gov/Archives/edgar/data/123/0000000123-06-000001.txt",
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000123.json",
        "https://www.sec.gov/Archives/edgar/full-index/2025/QTR1/master.zip",
        "https://www.sec.gov/Archives/edgar/full-index/2006/QTR5/master.zip",
        URL + "?other=1",
        URL + "#fragment",
        URL.replace("www.sec.gov", "user@www.sec.gov"),
    ],
)
def test_only_registered_metadata_urls_are_permitted(url: str) -> None:
    with pytest.raises(module.AcquisitionError):
        module.validate_url(url)


def test_exact_76_index_plan_and_form_alias_routes() -> None:
    plan = module.index_plan()
    assert len(plan) == 76 and plan[0][0] == 2006 and plan[-1][0] == 2024
    assert all(module.validate_url(url) for _, _, url in plan)
    assert module.discovery_routes("INITIAL_13D") == (("ACTIVIST_13D", "CANDIDATE_SOURCE"),)
    assert module.discovery_routes("AMENDED_13D")[0][1] != "CANDIDATE_SOURCE"
    assert module.discovery_routes("PASSIVE_13G")[0][1] != "CANDIDATE_SOURCE"
    assert module.discovery_routes("4/A")[0][1] == "AMENDMENT_CONTEXT"
    assert len(module.discovery_routes("8-K")) == 3
    assert module.discovery_routes("S-4")[0][1] == "REGISTRATION_PROXY_PROVENANCE"
    assert not module.discovery_routes("")


def test_private_contact_is_required_and_newline_header_injection_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SEC_RESEARCH_CONTACT", raising=False)
    with pytest.raises(module.AcquisitionError, match="CONTACT_REQUIRED"):
        module.private_contact(tmp_path)
    (tmp_path / ".env").write_text(
        "\nSEC_RESEARCH_CONTACT=\nSEC_RESEARCH_CONTACT=Researcher <researcher@valid.test>\n",
        encoding="utf-8-sig",
    )
    assert module.private_contact(tmp_path) == "Researcher <researcher@valid.test>"
    monkeypatch.setenv("SEC_RESEARCH_CONTACT", "Researcher <a@valid.test>\r\nExtra: bad")
    with pytest.raises(module.AcquisitionError):
        module.private_contact(tmp_path)


def test_immutable_cache_reuse_and_private_header_redaction(tmp_path: Path) -> None:
    _, scheduler, snapshot = setup(tmp_path)
    transport = FakeTransport(response())
    with scheduler:
        first = client(tmp_path, snapshot, scheduler, transport)
        raw, record = first.fetch(URL)
        assert record["sha256"] == hashlib.sha256(raw).hexdigest()
        assert first.fetch(URL)[0] == raw
    assert len(transport.calls) == 1
    ledger = (snapshot / "requests.jsonl").read_text()
    assert "researcher@valid.test" not in ledger and AGENT not in ledger
    assert json.loads(ledger.splitlines()[-1])["cache_hit"] is True


def test_refresh_creates_new_immutable_object_version(tmp_path: Path) -> None:
    _, scheduler, snapshot = setup(tmp_path)
    newer = module.Response(200, {"content-type": "application/zip"}, zipped(raw_index(2007)))
    with scheduler:
        transport = FakeTransport(response(), newer)
        original = client(tmp_path, snapshot, scheduler, transport).fetch(URL)[1]
        updated = client(tmp_path, snapshot, scheduler, transport, refresh=True).fetch(URL)[1]
        assert original["sha256"] != updated["sha256"]
        assert module.Cache(tmp_path).read(original["sha256"])
        assert module.Cache(tmp_path).read(updated["sha256"])


def test_cache_corruption_is_not_silently_redownloaded(tmp_path: Path) -> None:
    _, scheduler, snapshot = setup(tmp_path)
    transport = FakeTransport(response())
    with scheduler:
        c = client(tmp_path, snapshot, scheduler, transport)
        _, record = c.fetch(URL)
        c.cache.object_path(record["sha256"]).write_bytes(b"corrupt synthetic bytes")
        with pytest.raises(module.AcquisitionError, match="CACHE_HASH_MISMATCH"):
            c.fetch(URL)
    assert len(transport.calls) == 1


def test_single_owner_lock_and_persistent_global_request_spacing(tmp_path: Path) -> None:
    clock, scheduler, _ = setup(tmp_path)
    with scheduler:
        first = scheduler.start(2000)
        second = scheduler.start(2000)
        assert second - first >= 0.5
        with (
            pytest.raises(module.AcquisitionError, match="ANOTHER_OWNER"),
            module.Scheduler(tmp_path),
        ):
            pass
    with module.Scheduler(tmp_path, clock.now, clock.sleep) as next_owner:
        assert next_owner.start(2000) - second >= 0.5
    assert not (tmp_path / "sec-acquisition.lock").exists()


def test_403_stops_batch_without_retry_or_ip_identity_rotation(tmp_path: Path) -> None:
    _, scheduler, snapshot = setup(tmp_path)
    transport = FakeTransport(response(403), response())
    with scheduler:
        with pytest.raises(module.AcquisitionError, match="HTTP_403"):
            client(tmp_path, snapshot, scheduler, transport).fetch(URL)
        with pytest.raises(module.AcquisitionError, match="BLOCKED"):
            client(tmp_path, snapshot, scheduler, transport).fetch(URL)
    assert len(transport.calls) == 1


def test_two_consecutive_429_stop_and_retry_after_is_honored(tmp_path: Path) -> None:
    clock, scheduler, snapshot = setup(tmp_path)
    transport = FakeTransport(
        response(429, **{"retry-after": "3"}), response(429, **{"retry-after": "4"}), response()
    )
    with scheduler, pytest.raises(module.AcquisitionError, match="TWO_CONSECUTIVE_429"):
        client(tmp_path, snapshot, scheduler, transport).fetch(URL)
    assert len(transport.calls) == 2
    assert sum(clock.delays) >= 3
    assert scheduler.state()["not_before"] >= clock.now() + 4


def test_long_retry_after_defers_instead_of_shortening_server_delay(tmp_path: Path) -> None:
    clock, scheduler, snapshot = setup(tmp_path)
    transport = FakeTransport(response(503, **{"retry-after": "600"}), response())
    with scheduler, pytest.raises(module.AcquisitionError, match="DEFERRED"):
        client(tmp_path, snapshot, scheduler, transport, deadline=clock.now() + 100).fetch(URL)
    assert len(transport.calls) == 1 and clock.delays == []
    assert scheduler.state()["not_before"] == clock.now() + 600


def test_max_five_total_attempts_including_resumed_command(tmp_path: Path) -> None:
    _, scheduler, snapshot = setup(tmp_path)
    transport = FakeTransport(*(response(503) for _ in range(5)))
    with scheduler:
        with pytest.raises(module.AcquisitionError, match="FIVE_ATTEMPTS"):
            client(tmp_path, snapshot, scheduler, transport).fetch(URL)
        with pytest.raises(module.AcquisitionError, match="FIVE_ATTEMPTS"):
            client(tmp_path, snapshot, scheduler, transport).fetch(URL)
    assert len(transport.calls) == 5


def test_transport_error_retry_and_registered_redirect_share_rate_control(tmp_path: Path) -> None:
    clock, scheduler, snapshot = setup(tmp_path)
    transport = FakeTransport(OSError("synthetic transport error"), response())
    with scheduler:
        client(tmp_path, snapshot, scheduler, transport).fetch(URL)
    assert len(transport.calls) == 2 and sum(clock.delays) >= 1
    assert len({agent for _, agent in transport.calls}) == 1


def test_off_registry_redirect_fails_before_following_it(tmp_path: Path) -> None:
    _, scheduler, snapshot = setup(tmp_path)
    transport = FakeTransport(response(302, location="https://finance.yahoo.com/"))
    with scheduler, pytest.raises(module.AcquisitionError):
        client(tmp_path, snapshot, scheduler, transport).fetch(URL)
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "answer",
    [
        module.Response(200, {"content-type": "text/html"}, b"<html>error</html>"),
        module.Response(200, {"content-type": "application/zip"}, b"<html>error</html>"),
        module.Response(404, {"content-type": "text/plain"}, b"missing"),
    ],
)
def test_content_status_errors_never_become_cached_success(tmp_path: Path, answer: Any) -> None:
    _, scheduler, snapshot = setup(tmp_path)
    with scheduler, pytest.raises(module.AcquisitionError):
        client(tmp_path, snapshot, scheduler, FakeTransport(answer)).fetch(URL)
    assert not (tmp_path / "object-versions.jsonl").exists()


def test_zip_member_validation_and_lossless_encoding(tmp_path: Path) -> None:
    with pytest.raises(module.AcquisitionError, match="MEMBER"):
        module.index_payload(zipped(raw_index(), "../master.idx"), URL)
    content, encoding = module.index_payload(zipped(raw_index()), URL)
    assert len(list(module.parse_master(content, encoding))) == 3
    content = raw_index().replace(b"Synthetic Issuer 1", b"Synthetic Issuer \xff")
    _, encoding = module.index_payload(zipped(content), URL)
    assert encoding == "latin-1-lossless"
    assert len(list(module.parse_master(content, encoding))) == 3


def test_index_dedup_and_nonadditive_discovery_route_reconciliation(tmp_path: Path) -> None:
    aliases = {"4": "4", "SC 13D": "INITIAL_13D", "8-K": "8-K"}
    raw = zipped(raw_index(duplicate=True))
    with sqlite3.connect(":memory:") as db:
        db.executescript(module.SCHEMA)
        first = module.ingest_index(db, 2006, 1, URL, raw, {"sha256": module.sha(raw)}, aliases)
        assert first["raw_rows"] == 4 and first["unique_rows"] == 3 and first["duplicates"] == 1
        summary = module.export_census(db, tmp_path, False)
        assert summary["complete_range_totals"] is None
        assert summary["observed_partial_totals"]["registered_records"] == 3
        assert sum(row["records"] for row in summary["year_family_routes"]) == 5
        db.set_authorizer(module.sqlite_authorizer)
        with pytest.raises(sqlite3.DatabaseError):
            db.execute("ATTACH DATABASE ':memory:' AS forbidden")


def test_malformed_metadata_row_rolls_back_the_index_transaction() -> None:
    raw = raw_index() + b"123|Broken|4|not-a-date|edgar/data/123/0000000123-06-000099.txt\n"
    with sqlite3.connect(":memory:") as db:
        db.executescript(module.SCHEMA)
        with pytest.raises(module.AcquisitionError, match="DATE_INVALID"):
            module.ingest_index(db, 2006, 1, URL, zipped(raw), {"sha256": "a" * 64}, {"4": "4"})
        assert db.execute("SELECT count(*) FROM seen_records").fetchone()[0] == 0


def test_http_date_retry_after() -> None:
    now = datetime(2026, 11, 4, 12, tzinfo=UTC)
    assert module.retry_after_seconds("Wed, 04 Nov 2026 12:00:30 GMT", now) == 30
    assert module.retry_after_seconds("invalid", now) == 0


def test_full_76_index_census_with_synthetic_transport(tmp_path: Path) -> None:
    class Generated:
        def get(self, url: str, agent: str) -> Any:
            parts = url.split("/")
            year, quarter = int(parts[-3]), int(parts[-2][-1])
            return module.Response(
                200, {"content-type": "application/zip"}, zipped(raw_index(year, quarter))
            )

    _, scheduler, snapshot = setup(tmp_path)
    with scheduler:
        c = client(tmp_path, snapshot, scheduler, Generated())
        result = module.run_census(
            snapshot, c, {"4": "4", "SC 13D": "INITIAL_13D", "8-K": "8-K"}, {}
        )
    assert result["status"] == "COMPLETE_METADATA_CENSUS"
    summary = json.loads((snapshot / "census-summary.json").read_bytes())
    assert summary["complete_range_totals"]["unique_rows"] == 228
    assert summary["completed_indices"] == 76
    assert all(v == 0 for v in summary["firewall_counters"].values())
    manifest = json.loads((snapshot / "authority-manifest.json").read_bytes())
    for name, digest in manifest["files"].items():
        assert module.sha((snapshot / name).read_bytes()) == digest
    assert "researcher@valid.test" not in (snapshot / "requests.jsonl").read_text()


@pytest.mark.parametrize(
    "probe",
    [
        "__import__('wrds')",
        "__import__('trading_research.performance')",
        "__import__('trading_research.backtesting')",
        "socket.socket()",
        "subprocess.run([sys.executable, '-c', 'pass'])",
        "open(str(root/'synthetic-price-file.csv'), 'rb')",
        "sqlite3.connect(str(root/'synthetic-CRSP.sqlite3'))",
    ],
)
def test_actual_workflow_firewall_denies_market_and_escape_capabilities(probe: str) -> None:
    bootstrap = (
        "import runpy,sys,socket,subprocess,sqlite3;from pathlib import Path;"
        f"root=Path({str(ROOT)!r});m=runpy.run_path({str(SCRIPT)!r});"
        "m['install_firewall']();"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", bootstrap + probe],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0 and "FirewallError" in result.stderr


def test_extension_preflight_preserves_original_protocol() -> None:
    status = module.preflight()
    assert status["protocol"] == "PASS"
    assert status["manifest_sha256"] == module.FROZEN_SHA


def test_failed_attempt_budget_cannot_be_reset_by_new_snapshot(tmp_path: Path) -> None:
    _, scheduler, snapshot = setup(tmp_path)
    transport = FakeTransport(*(response(503) for _ in range(5)))
    with scheduler:
        with pytest.raises(module.AcquisitionError, match="FIVE_ATTEMPTS"):
            client(tmp_path, snapshot, scheduler, transport).fetch(URL)
        another = tmp_path / "snapshots" / "another"
        another.mkdir()
        with pytest.raises(module.AcquisitionError, match="FIVE_ATTEMPTS"):
            client(tmp_path, another, scheduler, transport).fetch(URL)
    assert len(transport.calls) == 5


def test_successful_refresh_gets_its_own_bounded_retrieval_budget(tmp_path: Path) -> None:
    _, scheduler, snapshot = setup(tmp_path)
    transport = FakeTransport(*(response() for _ in range(6)))
    with scheduler:
        for _ in range(6):
            client(tmp_path, snapshot, scheduler, transport, refresh=True).fetch(URL)
    assert len(transport.calls) == 6
    assert scheduler.attempts_used(URL) == 0


def test_request_accounting_has_registered_fields_and_never_logs_contact(tmp_path: Path) -> None:
    _, scheduler, snapshot = setup(tmp_path)
    with scheduler:
        c = client(tmp_path, snapshot, scheduler, FakeTransport(response()))
        c.fetch(URL)
        c.fetch(URL)
    rows = [json.loads(line) for line in (snapshot / "requests.jsonl").read_bytes().splitlines()]
    required = {
        "request_id",
        "utc",
        "host",
        "path",
        "attempt",
        "status",
        "headers_hash",
        "response_sha256",
        "bytes",
        "cache_hit",
        "delay",
        "deferred_reason",
    }
    assert all(required <= set(row) for row in rows)
    assert rows[0]["response_sha256"] == rows[1]["response_sha256"]
    assert all("researcher@valid.test" not in json.dumps(row) for row in rows)


def test_https_non200_preserves_status_without_reading_error_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Reply:
        status = 403

        def getheaders(self) -> list[tuple[str, str]]:
            return [("Content-Length", "999999999"), ("Content-Type", "text/html")]

        def read(self, amount: int) -> bytes:
            raise AssertionError("Non-200 body must not be downloaded")

    class Connection:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def request(self, *args: Any, **kwargs: Any) -> None:
            pass

        def getresponse(self) -> Reply:
            return Reply()

        def close(self) -> None:
            pass

    monkeypatch.setattr(module.http.client, "HTTPSConnection", Connection)
    result = module.HttpsTransport().get(URL, AGENT)
    assert result.status == 403 and result.body == b""


def test_batched_dedup_matches_same_identity_across_index_files() -> None:
    aliases = {"4": "4", "SC 13D": "INITIAL_13D", "8-K": "8-K"}
    raw = zipped(raw_index(duplicate=True))
    with sqlite3.connect(":memory:") as db:
        db.executescript(module.SCHEMA)
        a = module.ingest_index(db, 2006, 1, URL, raw, {"sha256": module.sha(raw)}, aliases)
        b = module.ingest_index(
            db,
            2006,
            2,
            URL.replace("QTR1", "QTR2"),
            raw,
            {"sha256": module.sha(raw)},
            aliases,
        )
        assert a["unique_rows"] == 3 and a["duplicates"] == 1
        assert b["unique_rows"] == 0 and b["duplicates"] == 4
        assert b["registered_records"] == 0 and b["unique_filing_paths"] == 0
        assert db.execute("SELECT sum(raw_rows),sum(unique_rows) FROM form_counts").fetchone() == (
            8,
            3,
        )


def test_local_firewall_denial_is_diagnostic_and_never_retried(tmp_path: Path) -> None:
    _, scheduler, snapshot = setup(tmp_path)
    transport = FakeTransport(module.FirewallError("METADATA_ONLY_IMPORT_DENIED"), response())
    with scheduler, pytest.raises(module.AcquisitionError, match="LOCAL_FIREWALL_DENIAL"):
        client(tmp_path, snapshot, scheduler, transport).fetch(URL)
    assert len(transport.calls) == 1
    rows = [json.loads(line) for line in (snapshot / "requests.jsonl").read_text().splitlines()]
    assert rows[0]["reason"] == "LOCAL_FIREWALL_DENIAL:METADATA_ONLY_IMPORT_DENIED"


class CheckpointTransport:
    def __init__(self, stop_after: int | None = None) -> None:
        self.calls: list[str] = []
        self.stop_after = stop_after

    def get(self, url: str, agent: str) -> Any:
        if self.stop_after is not None and len(self.calls) == self.stop_after:
            raise module.AcquisitionError("SYNTHETIC_OPERATIONAL_STOP")
        self.calls.append(url)
        parts = url.split("/")
        year, quarter = int(parts[-3]), int(parts[-2][-1])
        # A repeated five-field record crosses the checkpoint boundary.
        common = (
            b"123|Synthetic Issuer 3|8-K|2006-03-01|"
            b"edgar/data/123/0000000123-06-000013.txt\n"
        )
        return module.Response(
            200, {"content-type": "application/zip"}, zipped(raw_index(year, quarter) + common)
        )


def checkpoint_provenance() -> dict[str, Any]:
    return {
        "manifest_sha256": module.FROZEN_SHA,
        "acquisition_code_hashes": {
            "tools/corporate_events/sec_census.py": module.file_sha(SCRIPT)
        },
    }


def partial_checkpoint(tmp_path: Path) -> tuple[Any, Path, dict[str, Any]]:
    _, scheduler, snapshot = setup(tmp_path)
    provenance = checkpoint_provenance()
    with scheduler:
        result = module.run_census(
            snapshot, client(tmp_path, snapshot, scheduler, CheckpointTransport(2)),
            {"4": "4", "SC 13D": "INITIAL_13D", "8-K": "8-K"}, provenance,
        )
    assert result["completed_indices"] == 2
    return scheduler, snapshot, provenance


def test_checkpoint_continuation_matches_uninterrupted_census(tmp_path: Path) -> None:
    scheduler, original, provenance = partial_checkpoint(tmp_path)
    before = {p.name: module.file_sha(p) for p in original.iterdir()}
    resumed = original.parent / "continued"
    resumed.mkdir()
    transport = CheckpointTransport()
    with scheduler:
        result = module.run_census(
            resumed, client(tmp_path, resumed, scheduler, transport),
            {"4": "4", "SC 13D": "INITIAL_13D", "8-K": "8-K"}, provenance, original,
        )
    assert result["status"] == "COMPLETE_METADATA_CENSUS"
    assert transport.calls == [row[2] for row in module.index_plan()[2:]]
    assert before == {p.name: module.file_sha(p) for p in original.iterdir()}
    continuation = json.loads((resumed / "continuation.json").read_bytes())
    assert continuation["completed_indices_reused"] == 2
    assert continuation["attempt_budgets_reset"] is False
    reference = original.parent / "reference"
    reference.mkdir()
    with scheduler:
        module.run_census(
            reference, client(tmp_path, reference, scheduler, CheckpointTransport()),
            {"4": "4", "SC 13D": "INITIAL_13D", "8-K": "8-K"}, provenance,
        )
    actual = json.loads((resumed / "census-summary.json").read_bytes())
    assert actual == json.loads((reference / "census-summary.json").read_bytes())
    assert actual["complete_range_totals"]["duplicates"] == 76
    assert actual["complete_range_totals"]["unique_rows"] == 228


@pytest.mark.parametrize("fault", ["artifact", "producer", "schema", "prefix", "raw", "scope"])
def test_checkpoint_rejects_incompatible_or_corrupted_prefix(
    tmp_path: Path, fault: str
) -> None:
    scheduler, original, provenance = partial_checkpoint(tmp_path)
    authority_path = original / "authority-manifest.json"
    authority = json.loads(authority_path.read_bytes())
    if fault == "artifact":
        with (original / "census-summary.json").open("ab") as target:
            target.write(b" ")
    elif fault == "producer":
        authority["protocol"]["acquisition_code_hashes"][
            "tools/corporate_events/sec_census.py"
        ] = "0" * 64
    elif fault == "schema":
        with sqlite3.connect(original / "metadata-census.sqlite3") as db:
            db.execute("CREATE TABLE unexpected_input (value TEXT)")
        db.close()
        authority["files"]["metadata-census.sqlite3"] = module.file_sha(
            original / "metadata-census.sqlite3"
        )
    elif fault == "prefix":
        records = (original / "source-objects.jsonl").read_bytes().splitlines()
        (original / "source-objects.jsonl").write_bytes(b"\n".join(reversed(records)) + b"\n")
        authority["files"]["source-objects.jsonl"] = module.file_sha(
            original / "source-objects.jsonl"
        )
    elif fault == "raw":
        first = json.loads((original / "source-objects.jsonl").read_bytes().splitlines()[0])
        (tmp_path / first["object_locator"]).write_bytes(b"corrupt synthetic bytes")
    elif fault == "scope":
        authority["files"]["../unregistered-input"] = "0" * 64
    authority_path.write_bytes(module.json_bytes(authority))
    resumed = original.parent / "rejected"
    resumed.mkdir()
    transport = CheckpointTransport()
    with scheduler, pytest.raises(module.AcquisitionError):
        module.run_census(
            resumed, client(tmp_path, resumed, scheduler, transport), {}, provenance, original
        )
    assert transport.calls == []



def test_blank_source_company_name_is_preserved_not_skipped_or_inferred(tmp_path: Path) -> None:
    blank = raw_index().replace(b"Synthetic Issuer 1", b"")
    rows = list(module.parse_master(blank, "utf-8-sig"))
    assert len(rows) == 3 and rows[0].company_name == ""
    assert rows[0].cik == "123" and rows[0].form == "4"
    normal = list(module.parse_master(raw_index(), "utf-8-sig"))[0]
    assert rows[0].record_hash() != normal.record_hash()
    with sqlite3.connect(":memory:") as db:
        db.executescript(module.SCHEMA)
        totals = module.ingest_index(
            db, 2006, 1, URL, zipped(blank), {"sha256": module.sha(zipped(blank))}, {"4": "4"}
        )
        assert totals["raw_rows"] == totals["unique_rows"] == 3
        assert totals["registered_records"] == 1
