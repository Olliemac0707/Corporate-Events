"""Synthetic-only regression tests for the SEC event protocol and capability boundary."""

from __future__ import annotations

import copy
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tomllib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/corporate_events/phase2.py"
P = ROOT / "protocols/corporate-event-alpha-v1"
spec = importlib.util.spec_from_file_location("sec_only_phase2", SCRIPT)
assert spec is not None and spec.loader is not None
runtime: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def protocol() -> dict[str, Any]:
    return tomllib.loads((P / "event-data-protocol.toml").read_text(encoding="utf-8"))


def database() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.executescript((P / "event-schema.sql").read_text(encoding="utf-8"))
    db.execute(
        "INSERT INTO issuer_identities VALUES (?,?,?,?,?)",
        ("issuer", "123", "0000000123", "RESOLVED_SEC", "synthetic issuer evidence"),
    )
    db.execute(
        "INSERT INTO disclosures VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "disclosure",
            "issuer",
            "synthetic-accession",
            "8-K",
            "8-K",
            "synthetic.txt",
            "a" * 64,
            None,
            "BODY",
            "2010-01-04",
            "2026-11-04T00:00:00Z",
        ),
    )
    db.execute(
        "INSERT INTO source_evidence VALUES (?,?,?,?,?,?,?,?)",
        (
            "evidence",
            "disclosure",
            "a" * 64,
            "objects/synthetic",
            "paragraph:1",
            "announcement",
            "2010-01-04T14:00:00Z",
            "synthetic fixture",
        ),
    )
    db.execute(
        "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "event",
            "SHARE_REPURCHASE",
            "issuer",
            "disclosure",
            "synthetic-action",
            None,
            "2010-01-04",
            "DEVELOPMENT",
            "CANDIDATE",
            "2026-11-04T00:00:00Z",
        ),
    )
    return db


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("123", "0000000123"),
        (" 0000123 ", "0000000123"),
        ("9999999999", "9999999999"),
        ("0", None),
        ("0000000000", None),
        ("123.0", None),
        ("1e3", None),
        ("-123", None),
        ("１２３", None),
        ("10000000000", None),
        (None, None),
    ],
)
def test_cik_preserves_missing_invalid_without_guess(raw: str | None, expected: str | None) -> None:
    assert runtime.canonical_cik(raw) == expected


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (None, "UNRESOLVED_DATE"),
        (date(2005, 12, 31), "PRE_STUDY_CONTEXT"),
        (date(2006, 1, 1), "DEVELOPMENT"),
        (date(2015, 12, 31), "DEVELOPMENT"),
        (date(2016, 1, 1), "VALIDATION"),
        (date(2020, 12, 31), "VALIDATION"),
        (date(2021, 1, 1), "SEALED_HOLDOUT"),
        (date(2024, 12, 31), "SEALED_HOLDOUT"),
        (date(2025, 1, 1), "OUTSIDE_INITIAL_STUDY"),
        (date(2030, 1, 1), "OUTSIDE_INITIAL_STUDY"),
    ],
)
def test_exact_split_boundaries(day: date | None, expected: str) -> None:
    assert runtime.split_label(day) == expected


def test_no_acceptance_or_economic_date_shortcut() -> None:
    assert runtime.public_bounds() == (None, None, "TIMESTAMP_UNRESOLVED")
    assert runtime.public_bounds(verified=datetime(2010, 1, 4, tzinfo=UTC))[2] == (
        "TIMESTAMP_UNRESOLVED"
    )
    assert runtime.ready_after(None, datetime(2010, 1, 4, tzinfo=UTC)) is None


def test_later_classification_dependency_delays_strict_open_definition() -> None:
    public = datetime(2010, 1, 4, 14, 0, tzinfo=UTC)
    later = datetime(2010, 1, 5, 14, 20, tzinfo=UTC)
    boundary = runtime.ready_after(public, later)
    assert boundary == later + timedelta(seconds=900)
    # Equality is ineligible; this uses invented instants, not an exchange calendar.
    assert not boundary > boundary
    assert boundary + timedelta(microseconds=1) > boundary


def test_independently_evidenced_day_bound_and_dst() -> None:
    winter = runtime.public_bounds(dissemination_day=date(2024, 3, 8), evidence_id="fixture")
    summer = runtime.public_bounds(dissemination_day=date(2024, 3, 11), evidence_id="fixture")
    assert winter[0].hour == 5 and summer[0].hour == 4
    assert summer[1] == datetime(2024, 3, 12, 3, 59, 59, 999999, tzinfo=UTC)
    assert summer[2] == "EVIDENCED_DISSEMINATION_DAY"
    assert (
        runtime.public_bounds(
            verified=datetime(2024, 3, 12, 14, tzinfo=UTC),
            dissemination_day=date(2024, 3, 11),
            evidence_id="fixture",
        )[2]
        == "TIMESTAMP_UNRESOLVED"
    )
    with pytest.raises(ValueError, match="Timezone-aware"):
        runtime.public_bounds(verified=datetime(2024, 3, 11), evidence_id="fixture")


def test_raw_acceptance_is_normalized_but_never_public_time() -> None:
    assert runtime.acceptance_datetimes("20240311165759") is None
    et, utc = runtime.acceptance_datetimes("20240311165759", source_zone="America/New_York")
    assert et.hour == 16 and utc.hour == 20
    assert runtime.acceptance_datetimes("20240310023000", source_zone="America/New_York") is None
    assert runtime.acceptance_datetimes("20241103013000", source_zone="America/New_York") is None
    assert runtime.acceptance_datetimes("bad", source_zone="America/New_York") is None
    assert runtime.public_bounds()[2] == "TIMESTAMP_UNRESOLVED"


def test_nullable_mapping_does_not_prevent_event_retention() -> None:
    with database() as db:
        db.execute("INSERT INTO event_security_mapping_placeholders(event_id) VALUES ('event')")
        assert db.execute(
            "SELECT target_authority,target_identifier,status "
            "FROM event_security_mapping_placeholders"
        ).fetchone() == (None, None, "SECURITY_MAPPING_UNRESOLVED")
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE events SET family='ACTIVIST_13D'",
        "DELETE FROM events",
        "UPDATE source_evidence SET evidence_note='later fact'",
        "DELETE FROM disclosures",
    ],
)
def test_immutable_original_records_require_revisions(mutation: str) -> None:
    with database() as db, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute(mutation)


def test_new_family_and_vendor_mapping_are_rejected() -> None:
    with database() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    "bad",
                    "EARNINGS_SURPRISE",
                    "issuer",
                    "disclosure",
                    "x",
                    None,
                    "2010-01-04",
                    "DEVELOPMENT",
                    "CANDIDATE",
                    "fixture",
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO event_security_mapping_placeholders VALUES (?,?,?,?,?)",
                ("event", None, "vendor", "12345", "SECURITY_MAPPING_UNRESOLVED"),
            )


def test_schema_has_no_market_fields_and_wrong_family_facts_rejected() -> None:
    assert runtime.validate_schema((P / "event-schema.sql").read_text("utf-8")) == 20
    with database() as db, pytest.raises(sqlite3.IntegrityError, match="wrong event family"):
        db.execute(
            "INSERT INTO activist_facts VALUES (?,?,?,?,?,?,?)",
            ("event", "ACTIVIST_INTENT_SUPPORTED", 1, "evidence", "0.06", "100", "group"),
        )


def test_split_label_cannot_be_chosen_independently_of_disclosure_date() -> None:
    with database() as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "wrong-split",
                "SHARE_REPURCHASE",
                "issuer",
                "disclosure",
                "other",
                None,
                "2021-01-01",
                "DEVELOPMENT",
                "CANDIDATE",
                "fixture",
            ),
        )


def test_database_attach_is_forbidden_even_for_synthetic_database() -> None:
    with database() as db:
        db.set_authorizer(runtime.database_authorizer)
        with pytest.raises(sqlite3.DatabaseError, match="authorized"):
            db.execute("ATTACH DATABASE ':memory:' AS another")


def test_protocol_and_gate_invariants() -> None:
    p = protocol()
    runtime.validate_protocol(p)
    assert not p["transition"]["phase3_authorized"]
    assert not p["transition"]["phase4plus_authorized"]
    assert p["horizon_maturity"]["forbidden"] == [
        "shorten_horizon",
        "substitute_last_date",
        "carry_stale_price",
        "delete_event",
        "move_event_sample_end",
    ]
    assert set(p["missingness"]["codes"]) == set(runtime.CODES)
    assert p["review"]["qa_strata"][-1] == "parser_candidate_vs_independent_source_control"
    assert p["review"]["qa_quota_per_nonempty_cell"] == 5


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("scope", "families", ["SHARE_REPURCHASE"]),
        ("public_clock", "operational_buffer_seconds", 600),
        ("terminal_gate", "hard_return_gate", False),
        ("spac_gate", "phase3_dated_lifecycle_required", False),
        ("transition", "phase3_authorized", True),
        ("normalized_model", "price_data_required", True),
        ("entry", "materialize_phase2_session", True),
    ],
)
def test_material_gate_mutations_fail_validation(section: str, key: str, value: object) -> None:
    p = copy.deepcopy(protocol())
    p[section][key] = value
    with pytest.raises(ValueError):
        runtime.validate_protocol(p)


def test_qa_rank_is_deterministic_and_not_outcome_based() -> None:
    args = ("SHARE_REPURCHASE", "2006-2012", "8-K", "BODY", "synthetic", "a" * 64)
    assert runtime.qa_rank(*args) == runtime.qa_rank(*args)
    assert len(runtime.qa_rank(*args)) == 64
    with pytest.raises(ValueError):
        runtime.qa_rank("bad|key", *args[1:])


@pytest.mark.parametrize(
    "name",
    [
        "wrds",
        "psycopg2",
        "sqlalchemy",
        "yfinance",
        "pandas",
        "trading_research",
        "crsp_a_stock",
        "crsp_a_ccm",
        "subprocess",
        "ctypes",
        "socket",
    ],
)
def test_provider_performance_and_escape_imports_denied(name: str) -> None:
    assert not runtime.import_allowed(name)
    assert runtime.import_allowed("json")


@pytest.mark.parametrize(
    "probe",
    [
        "__import__('wrds')",
        "__import__('trading_research.performance')",
        "importlib.import_module('yfinance')",
        "socket.socket()",
        "subprocess.run([sys.executable, '-c', 'pass'])",
        "open(str(root/'reports'/'synthetic-denied-input.txt'), 'rb')",
        "open(str(root/'synthetic-denied-write.txt'), 'w')",
        "sqlite3.connect(str(root/'reports'/'synthetic-denied.sqlite3'))",
    ],
)
def test_actual_guard_blocks_capabilities_before_access(probe: str) -> None:
    # Everything is synthetic. No prospective path or vendor dataset is opened.
    bootstrap = (
        "import runpy,sys,socket,subprocess,sqlite3,importlib;from pathlib import Path;"
        f"root=Path({str(ROOT)!r});m=runpy.run_path({str(SCRIPT)!r});"
        "m['install_firewall']();"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", bootstrap + probe],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "FirewallError" in completed.stderr
    assert "Phase2" in completed.stderr


def test_manifest_and_supported_isolated_command() -> None:
    assert len(runtime.verify_manifest()) == 64
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), "validate"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["market_access"] == "DENIED"


def test_nonisolated_launch_fails_closed() -> None:
    completed = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "validate"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "Use python -I -S -B" in completed.stderr


def test_manifest_detects_tampered_schema(tmp_path: Path) -> None:
    for name in (*runtime.FREEZE_PATHS, "protocols/corporate-event-alpha-v1/freeze-manifest.json"):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    target = tmp_path / "protocols/corporate-event-alpha-v1/event-schema.sql"
    target.write_text("CREATE TABLE forbidden_result(value REAL);", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        runtime.verify_manifest(tmp_path)


def test_independent_qa_control_does_not_require_an_event_or_vendor_mapping() -> None:
    with database() as db:
        db.execute(
            "INSERT INTO qa_source_controls VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "control",
                "disclosure",
                "SHARE_REPURCHASE",
                "2006-2012",
                "INDEPENDENT_SOURCE_CONTROL",
                1,
                "b" * 64,
                "source-fixture",
                "fixture",
                "independent",
            ),
        )
        assert db.execute("SELECT count(*) FROM qa_source_controls").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO qa_source_controls VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    "bad-control",
                    "disclosure",
                    "SHARE_REPURCHASE",
                    "2006-2012",
                    "INDEPENDENT_SOURCE_CONTROL",
                    0,
                    "c" * 64,
                    "source-fixture",
                    "fixture",
                    "filtered",
                ),
            )
    p = protocol()
    assert "permanent_security_class" not in p["dividends"]["baseline_keys"]
    assert "SEC_disclosed_security_class" in p["dividends"]["baseline_keys"]
    assert len(p["phase1c_status"]["confirmed_strengths"]) == 10
    assert len(p["phase1c_status"]["phase3_remaining"]) == 8
