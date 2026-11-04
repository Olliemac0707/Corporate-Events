"""Standalone Phase-2 SEC protocol boundary; no market/data-provider dependencies."""

from __future__ import annotations

import argparse
import builtins
import hashlib
import importlib.abc
import importlib.machinery
import json
import os
import re
import sqlite3
import sys
import tomllib
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import CodeType, ModuleType
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "protocols" / "corporate-event-alpha-v1"
PRIVATE = ROOT / "reports" / "corporate-event-alpha-v1" / "phase2-private"
FAMILIES = (
    "SHARE_REPURCHASE",
    "INSIDER_OPEN_MARKET_PURCHASE",
    "ACTIVIST_13D",
    "DIVIDEND_POSITIVE_CHANGE",
    "SPINOFF_ANNOUNCEMENT",
)
CODES = (
    "PRICE_SERIES_UNAVAILABLE",
    "SECURITY_MAPPING_UNRESOLVED",
    "DELISTED_PRICE_UNAVAILABLE",
    "NO_VALID_FIRST_TRADABLE_BAR",
    "CORPORATE_ACTION_AMBIGUITY",
    "TIMESTAMP_UNRESOLVED",
    "BASELINE_UNRESOLVED",
    "INTENT_UNRESOLVED",
    "SECURITY_TYPE_UNRESOLVED",
    "LEFT_CENSORED_HISTORY",
    "INSUFFICIENT_PRE_EVENT_HISTORY",
    "RIGHT_CENSORED_HORIZON",
    "SPLIT_WINDOW_CROSSES_BOUNDARY",
)
FREEZE_PATHS = (
    ".gitattributes",
    "protocols/corporate-event-alpha-v1/README.md",
    "protocols/corporate-event-alpha-v1/event-data-protocol.toml",
    "protocols/corporate-event-alpha-v1/event-schema.sql",
    "protocols/corporate-event-alpha-v1/source-evidence.md",
    "protocols/corporate-event-alpha-v1/America_New_York.tzif",
    "protocols/corporate-event-alpha-v1/timezone-LICENSE.txt",
    "protocols/corporate-event-alpha-v1/timezone-provenance.json",
    "tools/corporate_events/phase2.py",
    "tests/test_corporate_event_phase2.py",
)
DENIED_IMPORTS = frozenset(
    {
        "ctypes",
        "_ctypes",
        "subprocess",
        "multiprocessing",
        "_posixsubprocess",
        "_winapi",
        "socket",
        "_socket",
        "ssl",
        "_ssl",
        "http",
        "urllib",
        "ftplib",
        "runpy",
        "code",
        "codeop",
        "pickle",
        "marshal",
        "winreg",
        "msvcrt",
    }
)
FORBIDDEN_COLUMNS = frozenset(
    {
        "price",
        "prices",
        "return",
        "returns",
        "car",
        "bhar",
        "alpha",
        "pnl",
        "benchmark",
        "permno",
        "permco",
        "gvkey",
    }
)


class FirewallError(PermissionError):
    """A Phase-2 capability request is outside the frozen boundary."""


def import_allowed(name: str) -> bool:
    root = name.split(".", 1)[0]
    return root in sys.stdlib_module_names and root not in DENIED_IMPORTS


class StandardLibraryOnly(importlib.abc.MetaPathFinder):
    def find_spec(
        self, fullname: str, path: Sequence[str] | None, target: ModuleType | None = None
    ) -> importlib.machinery.ModuleSpec | None:
        if not import_allowed(fullname):
            raise FirewallError("Phase2 import is outside the standard-library allowlist")
        return None


def within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def install_firewall() -> None:
    """Restrict supported commands; not a sandbox for malicious replacement Python."""
    original_import = builtins.__import__
    stdlib = (Path(sys.base_prefix) / "Lib").resolve()
    code_files = {(ROOT / p).resolve() for p in FREEZE_PATHS}
    source = Path(__file__).resolve()
    code_files.add(source)
    protocol_root = PROTOCOL.resolve()
    private_root = PRIVATE.resolve()

    def allowed_path(value: object, *, write: bool = False) -> bool:
        if isinstance(value, int):
            return value in {0, 1, 2}
        if not isinstance(value, (str, bytes, os.PathLike)):
            return False
        raw = os.fsdecode(value)
        if raw == ":memory:":
            return not write
        # Reject prohibited segments before resolving/statting a supplied path.
        if "prospective" in raw.replace("\\", "/").lower().split("/"):
            return False
        p = Path(raw).resolve()
        if within(p, private_root):
            return True
        if write:
            return False
        if p in code_files or within(p, protocol_root):
            return True
        return within(p, stdlib) and "site-packages" not in {part.lower() for part in p.parts}

    def guarded_import(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] | None = None,
        level: int = 0,
    ) -> ModuleType:
        root_name = name
        if level:
            package = str((globals or {}).get("__package__", ""))
            root_name = package
        if not import_allowed(root_name):
            raise FirewallError("Phase2 import denied")
        return original_import(name, globals, locals, fromlist, level)

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event == "open":
            mode = args[1]
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            write = (isinstance(mode, str) and any(c in mode for c in "wax+")) or bool(
                flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            )
            if not allowed_path(args[0], write=write):
                raise FirewallError("Phase2 file capability denied")
        elif event in {"os.listdir", "os.scandir", "sqlite3.connect"}:
            if not allowed_path(args[0]):
                raise FirewallError("Phase2 directory/database capability denied")
        elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.truncate"}:
            if not allowed_path(args[0], write=True):
                raise FirewallError("Phase2 filesystem mutation denied")
        elif event in {"os.rename", "os.replace"}:
            if not all(allowed_path(v, write=True) for v in args[:2]):
                raise FirewallError("Phase2 filesystem move denied")
        elif (
            event.startswith(("socket.", "subprocess.", "ctypes."))
            or event.startswith(("os.exec", "os.spawn", "os.startfile"))
            or event in {"os.system", "os.posix_spawn", "os.fork"}
        ):
            raise FirewallError("Phase2 network/process/native capability denied")
        elif event == "exec" and isinstance(args[0], CodeType):
            filename = args[0].co_filename
            if not filename.startswith("<frozen ") and not allowed_path(filename):
                raise FirewallError("Phase2 arbitrary code execution denied")
        elif event in {"sqlite3.enable_load_extension", "sqlite3.load_extension"}:
            raise FirewallError("Phase2 SQLite extensions denied")

    builtins.__import__ = guarded_import
    sys.meta_path.insert(0, StandardLibraryOnly())
    sys.addaudithook(audit)


def canonical_cik(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip()
    if not re.fullmatch(r"[0-9]{1,10}", text) or int(text) == 0:
        return None
    return text.zfill(10)


def split_label(disclosure_day: date | None) -> str:
    if disclosure_day is None:
        return "UNRESOLVED_DATE"
    if disclosure_day < date(2006, 1, 1):
        return "PRE_STUDY_CONTEXT"
    if disclosure_day <= date(2015, 12, 31):
        return "DEVELOPMENT"
    if disclosure_day <= date(2020, 12, 31):
        return "VALIDATION"
    if disclosure_day <= date(2024, 12, 31):
        return "SEALED_HOLDOUT"
    return "OUTSIDE_INITIAL_STUDY"


def require_aware(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("Timezone-aware evidence required")
    return value.astimezone(UTC)


def new_york() -> ZoneInfo:
    with (PROTOCOL / "America_New_York.tzif").open("rb") as source:
        return ZoneInfo.from_file(source, key="America/New_York")


def acceptance_datetimes(
    raw: str,
    *,
    source_zone: str | None = None,
) -> tuple[datetime, datetime] | None:
    """SGML evidence normalization only; never establishes dissemination."""
    if source_zone != "America/New_York" or not re.fullmatch(r"[0-9]{14}", raw):
        return None
    try:
        local = datetime.strptime(raw, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    first = local.replace(tzinfo=new_york(), fold=0)
    second = local.replace(tzinfo=new_york(), fold=1)
    if first.utcoffset() != second.utcoffset():
        return None
    utc = first.astimezone(UTC)
    if utc.astimezone(new_york()).replace(tzinfo=None) != local:
        return None
    return first, utc


def public_bounds(
    *,
    verified: datetime | None = None,
    dissemination_day: date | None = None,
    evidence_id: str | None = None,
) -> tuple[datetime | None, datetime | None, str]:
    if not evidence_id:
        return None, None, "TIMESTAMP_UNRESOLVED"
    if (
        verified is not None
        and dissemination_day is not None
        and require_aware(verified).astimezone(new_york()).date() != dissemination_day
    ):
        return None, None, "TIMESTAMP_UNRESOLVED"
    if verified is not None:
        instant = require_aware(verified)
        return instant, instant, "VERIFIED_DISSEMINATION"
    if dissemination_day is not None:
        et = new_york()
        lower = datetime.combine(dissemination_day, time.min, et)
        upper = datetime.combine(dissemination_day, time.max, et)
        return lower.astimezone(UTC), upper.astimezone(UTC), "EVIDENCED_DISSEMINATION_DAY"
    return None, None, "TIMESTAMP_UNRESOLVED"


def ready_after(
    public_upper: datetime | None,
    classification_available_at: datetime | None,
) -> datetime | None:
    """Definition input only: a regular open must be STRICTLY AFTER this bound."""
    if public_upper is None or classification_available_at is None:
        return None
    return max(require_aware(public_upper), require_aware(classification_available_at)) + (
        timedelta(seconds=900)
    )


def qa_rank(
    family: str,
    era: str,
    form: str,
    source_kind: str,
    accession: str,
    source_sha256: str,
) -> str:
    parts = (
        "corporate-event-alpha-v1",
        "phase2-qa-v1",
        family,
        era,
        form,
        source_kind,
        accession,
        source_sha256,
    )
    if any("|" in p for p in parts):
        raise ValueError("QA identity cannot contain rank delimiters")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def verify_manifest(root: Path = ROOT) -> str:
    path = root / "protocols/corporate-event-alpha-v1/freeze-manifest.json"
    raw = path.read_bytes()
    manifest = json.loads(raw)
    entries = manifest["files"]
    if set(entries) != set(FREEZE_PATHS):
        raise ValueError("Freeze manifest scope differs from registered files")
    for relative, expected in entries.items():
        content = (root / relative).read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError("Frozen file hash mismatch: " + relative)
    return hashlib.sha256(raw).hexdigest()


def validate_protocol(protocol: dict[str, Any]) -> None:
    def check(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError(message)

    check(tuple(protocol["scope"]["families"]) == FAMILIES, "Exactly five frozen families")
    check(tuple(protocol["missingness"]["codes"]) == CODES, "Unresolved codes changed")
    h = protocol["historical_intent"]
    check(h["event_start"] == "2006-01-01" and h["event_end"] == "2024-12-31", "Event range")
    for key, expected in {
        "development": ["2006-01-01", "2015-12-31"],
        "validation": ["2016-01-01", "2020-12-31"],
        "sealed_holdout": ["2021-01-01", "2024-12-31"],
    }.items():
        check(h[key] == expected, "Split dates changed")
    check(protocol["public_clock"]["operational_buffer_seconds"] == 900, "Buffer changed")
    check(
        not protocol["public_clock"]["acceptance_plus_900_is_dissemination_proof"],
        "Acceptance cannot prove dissemination",
    )
    check("strictly greater" in protocol["entry"]["rule"], "Entry must be strict")
    check(not protocol["entry"]["materialize_phase2_session"], "Calendar is deferred")
    check(protocol["normalized_model"]["security_mapping_nullable"], "Nullable mapping")
    check(not protocol["normalized_model"]["price_data_required"], "No price dependency")
    check(not protocol["normalized_model"]["phase2_vendor_mapping"], "No vendor mapping")
    check(protocol["terminal_gate"]["hard_return_gate"], "Terminal gate lost")
    check(not protocol["terminal_gate"]["phase2_ingestion_gate"], "Distinct authority gates")
    check(len(protocol["terminal_gate"]["required_authorities"]) == 7, "Terminal cases")
    check(protocol["spac_gate"]["phase3_dated_lifecycle_required"], "SPAC authority gate")
    check(
        protocol["spac_gate"]["phase2_events_retained_regardless_of_eventual_eligibility"],
        "SPAC uncertainty cannot delete events",
    )
    check("RIGHT_CENSORED_HORIZON" in protocol["horizon_maturity"]["rule"], "Maturity rule")
    check(len(protocol["horizon_maturity"]["forbidden"]) == 5, "Censoring protections")
    check(not protocol["transition"]["phase3_authorized"], "Phase3 requires later task")
    check(not protocol["transition"]["phase4plus_authorized"], "Returns remain sealed")
    check(all(v == 0 for v in protocol["preclarification_firewall"].values()), "Firewall")
    check(protocol["fair_access"]["maximum_requests_per_second_global"] == 2, "SEC rate")
    check(protocol["fair_access"]["initial_concurrency"] == 1, "SEC concurrency")
    check(protocol["review"]["qa_quota_per_nonempty_cell"] == 5, "QA quota")
    check(
        set(protocol["source_registry"]["form_aliases"].values())
        >= {
            "4",
            "4/A",
            "INITIAL_13D",
            "AMENDED_13D",
            "PASSIVE_13G",
            "AMENDED_13G",
            "8-K",
            "8-K/A",
            "10-Q",
            "10-Q/A",
            "10-K",
            "10-K/A",
            "10-12B",
            "10-12B/A",
        },
        "Source form aliases",
    )
    required_states = {
        "repurchases": (
            "context_classes",
            {
                "EXTENSION_ONLY",
                "ROUTINE_ACTUAL_REPURCHASES",
                "EXPIRATION",
                "COMPLETION",
                "ASR_AGREEMENT",
                "TENDER_OFFER",
                "AMBIGUOUS",
            },
        ),
        "dividends": (
            "context_states",
            {"UNCHANGED", "DECREASE", "SUSPENSION", "RESUMPTION", "UNKNOWN_BASELINE"},
        ),
        "spinoffs": (
            "states",
            {
                "SPINOFF_INTENTION",
                "STRATEGIC_ALTERNATIVES_REVIEW",
                "ASSET_SALE",
                "CARVE_OUT_IPO",
                "SPLIT_OFF",
                "COMPLETION_ONLY_RECAP",
                "CANCELLED_PROPOSAL",
                "AMBIGUOUS",
            },
        ),
    }
    for family, (key, states) in required_states.items():
        check(set(protocol[family][key]) >= states, "Missing ontology context/rejection")
    check(
        set(protocol["form4"]["venue_states"])
        >= {"OPEN_MARKET_SUPPORTED", "PRIVATE_PURCHASE", "VENUE_UNRESOLVED"},
        "Venue evidence",
    )
    check(
        set(protocol["form4"]["discretion_states"])
        >= {"PLAN_TRANSACTION", "DISCRETION_UNRESOLVED"},
        "Discretion evidence",
    )
    check(
        set(protocol["form4"]["instrument_states"])
        >= {"DERIVATIVE_OR_OTHER", "SECURITY_CLASS_UNRESOLVED"},
        "Instrument evidence",
    )
    check(
        set(protocol["activist13d"]["intent_states"])
        == {"ACTIVIST_INTENT_SUPPORTED", "NONACTIVIST_INITIAL_13D", "INTENT_UNRESOLVED"},
        "13D initial intent states",
    )


def database_authorizer(
    action: int,
    arg1: str | None,
    arg2: str | None,
    db: str | None,
    trigger: str | None,
) -> int:
    if action in {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() == "load_extension":
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_PRAGMA and (arg1 or "").lower() in {
        "writable_schema",
        "temp_store_directory",
        "data_store_directory",
    }:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def validate_schema(schema: str) -> int:
    with sqlite3.connect(":memory:") as db:
        db.executescript(schema)
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            for row in db.execute(f'PRAGMA table_info("{table}")'):
                if FORBIDDEN_COLUMNS.intersection(row[1].lower().split("_")):
                    raise ValueError("Market/vendor field in event schema")
        expected = {
            "events",
            "event_revisions",
            "disclosures",
            "source_evidence",
            "event_clusters",
            "cluster_link_decisions",
            "classification_reviews",
            "issuer_identities",
            "event_security_mapping_placeholders",
            "timing_authority",
            "mapping_coverage_status",
        }
        if not expected.issubset(tables):
            raise ValueError("Normalized event relations missing")
        return len(tables)


def init_database(schema: str, manifest_sha256: str) -> Path:
    # Parents are prepared before the guard; only this dedicated directory is mutable.
    path = PRIVATE / "events.sqlite3"
    with path.open("xb"):
        pass
    with sqlite3.connect(path) as db:
        db.executescript(schema)
        db.execute("INSERT INTO protocol_binding VALUES (?,?)", ("1.0", manifest_sha256))
        db.set_authorizer(database_authorizer)
    return path


def main() -> int:
    if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode:
        print("Use python -I -S -B tools/corporate_events/phase2.py", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "init-db"))
    args = parser.parse_args()
    # Only the fixed, ignored run directory can be created; no user-selected filesystem root.
    if PRIVATE.resolve() != PRIVATE.absolute():
        print("Phase2 run directory cannot use symlink/junction aliases", file=sys.stderr)
        return 2
    PRIVATE.mkdir(parents=True, exist_ok=True)
    install_firewall()
    try:
        digest = verify_manifest()
        protocol = tomllib.loads((PROTOCOL / "event-data-protocol.toml").read_text("utf-8"))
        validate_protocol(protocol)
        schema = (PROTOCOL / "event-schema.sql").read_text("utf-8")
        count = validate_schema(schema)
        output: dict[str, object] = {
            "status": "VALID",
            "phase": "SEC_ONLY",
            "tables": count,
            "manifest_sha256": digest,
            "network": "DENIED",
            "market_access": "DENIED",
            "session_materialization": "DEFERRED_TO_PHASE3",
        }
        if args.command == "init-db":
            output["database"] = str(init_database(schema, digest))
        print(json.dumps(output, sort_keys=True))
        return 0
    except (ValueError, OSError, sqlite3.DatabaseError) as error:
        print(type(error).__name__ + ": " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
