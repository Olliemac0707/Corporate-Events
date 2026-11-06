"""SEC-only acquisition and historical-index census; no filing-body or market capability."""

from __future__ import annotations

import argparse
import builtins
import hashlib
import http.client
import importlib.abc
import importlib.machinery
import io
import json
import os
import random
import re
import sqlite3
import ssl
import subprocess
import sys
import time
import tomllib
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from types import CodeType, ModuleType
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "protocols/corporate-event-alpha-v1"
PRIVATE = ROOT / "reports/corporate-event-alpha-v1/phase2-private"
MAX_BYTES = 52_428_800
NETWORK_HOST: ContextVar[str | None] = ContextVar("sec_network_host", default=None)
ALLOWED_HOSTS = frozenset({"www.sec.gov", "data.sec.gov"})
FROZEN_SHA = "1461b12b2c2860564700868763bd4d9a8b9f94ab561ec8059dd9c1eadab464ff"
EXTENSION_FILES = (
    "tools/corporate_events/sec_census.py",
    "tools/corporate_events/.gitattributes",
    "tests/test_sec_acquisition.py",
    "tests/.gitattributes",
)
FIREWALL_COUNTERS = (
    "event_returns",
    "stock_returns",
    "abnormal_returns",
    "CAR",
    "BHAR",
    "event_price_joins",
    "CRSP_observation_queries",
    "CCM_queries",
    "Compustat_financial_observation_queries",
    "market_provider_queries",
    "backtests",
    "portfolio_simulations",
    "regressions",
    "bootstrap",
    "Monte_Carlo",
    "optimizations",
    "prospective_accesses",
)
DENIED_IMPORTS = frozenset(
    {
        "ctypes",
        "_ctypes",
        "subprocess",
        "multiprocessing",
        "_winapi",
        "runpy",
        "pickle",
        "marshal",
        "code",
        "codeop",
        "ftplib",
        "winreg",
        "msvcrt",
    }
)


class AcquisitionError(RuntimeError):
    """Safe reason code only; never include a private header or contact value."""


class FirewallError(PermissionError):
    """Workflow capability is outside the metadata-only boundary."""


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def utc_now() -> datetime:
    return datetime.now(UTC)


def json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n"
    ).encode()


def write_new(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as target:
        target.write(raw)


def append_record(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as target:
        target.write(json_bytes(value))
        target.flush()
        os.fsync(target.fileno())


def validate_url(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.sec.gov"
        or parsed.port not in {None, 443}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise AcquisitionError("URL_OUTSIDE_METADATA_REGISTRY")
    match = re.fullmatch(
        r"/Archives/edgar/full-index/(20[0-2][0-9])/QTR([1-4])/master\.(zip|idx)", parsed.path
    )
    if not match or not 2006 <= int(match[1]) <= 2024:
        raise AcquisitionError("URL_OUTSIDE_METADATA_REGISTRY")
    return parsed.hostname, parsed.path


def index_plan() -> list[tuple[int, int, str]]:
    return [
        (
            year,
            quarter,
            f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/master.zip",
        )
        for year in range(2006, 2025)
        for quarter in range(1, 5)
    ]


def private_contact(root: Path) -> str:
    value = os.environ.get("SEC_RESEARCH_CONTACT", "")
    env = root / ".env"
    if not value and env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            found = re.fullmatch(r"\s*(?:export\s+)?SEC_RESEARCH_CONTACT\s*=\s*(.*?)\s*", line)
            if found:
                value = found[1].strip().strip('"').strip("'")
    if (
        not value
        or len(value) > 300
        or not value.isascii()
        or any(char in value for char in "\r\n")
        or not re.search(r"[^\s@<>]+@[^\s@<>]+\.[A-Za-z]{2,}", value)
        or re.search(r"example\.(com|org|net)|placeholder|change.?me|noreply", value, re.I)
    ):
        raise AcquisitionError("REAL_PRIVATE_CONTACT_REQUIRED")
    return value


def preflight(root: Path = ROOT) -> dict[str, Any]:
    identity = json.loads((root / "phase2-protocol-identity.json").read_bytes())

    def git(*args: str) -> str:
        done = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
        return done.stdout.strip()

    if identity["expected_freeze_commit"] != "eb1d28dc8fa5b651baac6f0aff91f4db3182d00e":
        raise AcquisitionError("FREEZE_COMMIT_IDENTITY_FAILURE")
    git("merge-base", "--is-ancestor", identity["expected_freeze_commit"], "HEAD")
    tag = identity["expected_annotated_tag"]
    if (
        tag != "corporate-event-alpha-v1-phase1-event-data-protocol-v1-standalone"
        or git("cat-file", "-t", tag) != "tag"
        or git("rev-parse", tag + "^{}") != identity["expected_freeze_commit"]
    ):
        raise AcquisitionError("TAG_IDENTITY_FAILURE")
    raw = (root / "protocols/corporate-event-alpha-v1/freeze-manifest.json").read_bytes()
    if sha(raw) != FROZEN_SHA or identity["expected_freeze_manifest_sha256"] != FROZEN_SHA:
        raise AcquisitionError("MANIFEST_IDENTITY_FAILURE")
    for name, digest in json.loads(raw)["files"].items():
        if sha((root / name).read_bytes()) != digest:
            raise AcquisitionError("FROZEN_PAYLOAD_MISMATCH")
    extension = json.loads(
        (root / "protocols/corporate-event-alpha-v1/acquisition-extension.json").read_bytes()
    )
    if set(extension["files"]) != set(EXTENSION_FILES):
        raise AcquisitionError("EXTENSION_SCOPE_MISMATCH")
    for name, digest in extension["files"].items():
        if sha((root / name).read_bytes()) != digest:
            raise AcquisitionError("ACQUISITION_CODE_HASH_MISMATCH")
    return {
        "protocol": "PASS",
        "freeze_commit": identity["expected_freeze_commit"],
        "tag": tag,
        "manifest_sha256": FROZEN_SHA,
        "code_commit": git("rev-parse", "HEAD"),
        "acquisition_code_hashes": extension["files"],
        "tracked_changes_present": bool(git("status", "--porcelain", "--untracked-files=no")),
    }


def import_allowed(name: str) -> bool:
    first = name.split(".", 1)[0]
    return first in sys.stdlib_module_names and first not in DENIED_IMPORTS


class StdlibOnly(importlib.abc.MetaPathFinder):
    def find_spec(
        self, fullname: str, path: Sequence[str] | None, target: ModuleType | None = None
    ) -> importlib.machinery.ModuleSpec | None:
        if not import_allowed(fullname):
            raise FirewallError("METADATA_ONLY_IMPORT_DENIED")
        return None


def install_firewall() -> None:
    original = builtins.__import__
    library = (Path(sys.base_prefix) / "Lib").resolve()
    private = PRIVATE.resolve()
    protocol = PROTOCOL.resolve()
    sources = {(ROOT / name).resolve() for name in EXTENSION_FILES}
    sources.add((ROOT / "phase2-protocol-identity.json").resolve())

    def permitted(value: object, write: bool = False) -> bool:
        if isinstance(value, int):
            return value in {0, 1, 2}
        if not isinstance(value, (str, bytes, os.PathLike)):
            return False
        name = os.fsdecode(value)
        if name == ":memory:":
            return not write
        if "prospective" in name.replace("\\", "/").lower().split("/"):
            return False
        path = Path(name).resolve()
        if path == private or private in path.parents:
            return True
        if write:
            return False
        if path in sources or path == protocol or protocol in path.parents:
            return True
        return library in path.parents and "site-packages" not in {
            part.lower() for part in path.parts
        }

    def guarded_import(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] | None = None,
        level: int = 0,
    ) -> ModuleType:
        resolved = str((globals or {}).get("__package__", "")) if level else name
        if not import_allowed(resolved):
            raise FirewallError("METADATA_ONLY_IMPORT_DENIED")
        return original(name, globals, locals, fromlist, level)

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event == "open":
            mode = args[1]
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or bool(
                flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            )
            if not permitted(args[0], writing):
                raise FirewallError("METADATA_FILE_ACCESS_DENIED")
        elif event in {"os.listdir", "os.scandir", "sqlite3.connect"}:
            if not permitted(args[0]):
                raise FirewallError("METADATA_DIRECTORY_DATABASE_DENIED")
        elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.truncate"}:
            if not permitted(args[0], True):
                raise FirewallError("METADATA_FILE_MUTATION_DENIED")
        elif event in {"os.rename", "os.replace"}:
            if not all(permitted(arg, True) for arg in args[:2]):
                raise FirewallError("METADATA_FILE_MUTATION_DENIED")
        elif event.startswith(
            ("subprocess.", "ctypes.", "os.exec", "os.spawn", "os.startfile")
        ) or event in {"os.system", "os.fork", "os.posix_spawn"}:
            raise FirewallError("METADATA_PROCESS_NATIVE_DENIED")
        elif event.startswith("socket."):
            host = NETWORK_HOST.get()
            if host not in ALLOWED_HOSTS:
                raise FirewallError("NETWORK_OUTSIDE_SEC_TRANSPORT_DENIED")
            if (
                event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex"}
                and str(args[0]).lower() != host
            ):
                raise FirewallError("NETWORK_HOST_DENIED")
            if event == "socket.connect" and args[1][1] != 443:
                raise FirewallError("NETWORK_PORT_DENIED")
            if event in {"socket.bind", "socket.sendto"}:
                raise FirewallError("NETWORK_OPERATION_DENIED")
        elif event == "http.client.connect":
            if args[1] != NETWORK_HOST.get() or args[2] != 443:
                raise FirewallError("HTTPS_TARGET_DENIED")
        elif event in {"sqlite3.enable_load_extension", "sqlite3.load_extension"}:
            raise FirewallError("DATABASE_EXTENSION_DENIED")
        elif event == "exec" and isinstance(args[0], CodeType):
            filename = args[0].co_filename
            if not filename.startswith("<frozen ") and not permitted(filename):
                raise FirewallError("ARBITRARY_CODE_DENIED")

    builtins.__import__ = guarded_import
    sys.meta_path.insert(0, StdlibOnly())
    sys.addaudithook(audit)


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


class Transport(Protocol):
    def get(self, url: str, user_agent: str) -> Response: ...


class HttpsTransport:
    def __init__(self) -> None:
        self.context = ssl.create_default_context()
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2

    def get(self, url: str, user_agent: str) -> Response:
        host, path = validate_url(url)
        token = NETWORK_HOST.set(host)
        connection = http.client.HTTPSConnection(host, 443, timeout=30, context=self.context)
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "User-Agent": user_agent,
                    "Accept": "application/zip,text/plain,*/*",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            reply = connection.getresponse()
            headers = {name.lower(): value for name, value in reply.getheaders()}
            if reply.status != 200:
                # Status/redirect/retry metadata is sufficient; never accept an error body as data.
                return Response(reply.status, headers, b"")
            length = headers.get("content-length")
            if length and (not length.isdigit() or int(length) > MAX_BYTES):
                raise AcquisitionError("RESPONSE_LENGTH_INVALID")
            body = reply.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise AcquisitionError("RESPONSE_TOO_LARGE")
            if length and int(length) != len(body):
                raise AcquisitionError("RESPONSE_LENGTH_MISMATCH")
            return Response(reply.status, headers, body)
        finally:
            connection.close()
            NETWORK_HOST.reset(token)


def index_payload(raw: bytes, url: str) -> tuple[bytes, str]:
    validate_url(url)
    if url.endswith(".zip"):
        if not raw.startswith(b"PK"):
            raise AcquisitionError("INDEX_ZIP_SIGNATURE_INVALID")
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                members = archive.infolist()
                if len(members) != 1 or members[0].filename != "master.idx":
                    raise AcquisitionError("INDEX_ZIP_MEMBER_INVALID")
                if members[0].file_size > MAX_BYTES:
                    raise AcquisitionError("UNCOMPRESSED_INDEX_TOO_LARGE")
                content = archive.read(members[0])
        except AcquisitionError:
            raise
        except (zipfile.BadZipFile, RuntimeError) as error:
            raise AcquisitionError("INDEX_ZIP_INVALID") from error
    else:
        content = raw
    if not content or len(content) > MAX_BYTES:
        raise AcquisitionError("INDEX_CONTENT_SIZE_INVALID")
    try:
        text = content.decode("utf-8-sig")
        encoding = "utf-8-sig"
    except UnicodeDecodeError:
        text = content.decode("latin-1")
        encoding = "latin-1-lossless"
    if "CIK|Company Name|Form Type|Date Filed|Filename" not in text.splitlines()[:30]:
        raise AcquisitionError("MASTER_INDEX_HEADER_INVALID")
    return content, encoding


def retry_after_seconds(value: str | None, now: datetime) -> float:
    if not value:
        return 0.0
    if value.isdigit():
        return float(value)
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.utcoffset() is None:
            return 0.0
        return max(0.0, (parsed.astimezone(UTC) - now).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return 0.0


class Scheduler:
    """Single owner for this study; other identity clients/machines are forbidden."""

    def __init__(
        self,
        root: Path,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.root = root
        self.clock = clock
        self.sleep = sleep
        self.monotonic = time.monotonic if clock is time.time else clock
        self.lock_path = root / "sec-acquisition.lock"
        self.state_path = root / "scheduler-state.json"
        self.owned = False
        self.last_delay = 0.0

    def __enter__(self) -> Scheduler:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            write_new(self.lock_path, json_bytes({"pid": os.getpid(), "single_owner": True}))
        except FileExistsError as error:
            raise AcquisitionError("ANOTHER_OWNER_OR_STALE_LOCK_REQUIRES_REVIEW") from error
        self.owned = True
        return self

    def __exit__(self, *args: object) -> None:
        if self.owned:
            self.lock_path.unlink()
            self.owned = False

    def state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            return dict(json.loads(self.state_path.read_bytes()))
        except (ValueError, TypeError) as error:
            raise AcquisitionError("SCHEDULER_STATE_INVALID") from error

    def save(self, state: dict[str, Any]) -> None:
        self.state_path.write_bytes(json_bytes(state))

    def hold(self, seconds: float, reason: str) -> None:
        state = self.state()
        state["not_before"] = max(float(state.get("not_before", 0)), self.clock() + seconds)
        state["deferred_reason"] = reason
        self.save(state)

    def block(self, reason: str) -> None:
        state = self.state()
        state["blocked_reason"] = reason
        self.save(state)

    def start(self, deadline: float) -> float:
        if not self.owned:
            raise AcquisitionError("SINGLE_OWNER_LOCK_REQUIRED")
        state = self.state()
        if state.get("blocked_reason"):
            raise AcquisitionError("BATCH_BLOCKED_REQUIRES_REVIEW")
        target = max(
            float(state.get("last_start", -1)) + 0.5,
            float(state.get("not_before", 0)),
        )
        mono = self.monotonic()
        previous_mono = float(state.get("last_monotonic", -1))
        mono_wait = 0.5 if previous_mono > mono else max(0.0, previous_mono + 0.5 - mono)
        wait = max(0.0, target - self.clock(), mono_wait)
        if self.clock() + wait >= deadline or wait > 60:
            raise AcquisitionError("DEFERRED_UNTIL_REGISTERED_RETRY_TIME")
        self.last_delay = wait
        if wait:
            self.sleep(wait)
        # Persist before the request: retries and redirects share the same pacing.
        started = self.clock()
        state["last_start"] = started
        state["last_monotonic"] = self.monotonic()
        self.save(state)
        return started

    def attempts_used(self, url: str) -> int:
        return int(self.state().get("attempts_by_url", {}).get(url, 0))

    def record_attempt(self, url: str) -> None:
        state = self.state()
        budgets = state.setdefault("attempts_by_url", {})
        budgets[url] = int(budgets.get(url, 0)) + 1
        self.save(state)

    def record_success(self, url: str) -> None:
        state = self.state()
        state.setdefault("attempts_by_url", {})[url] = 0
        self.save(state)


class Cache:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.registry = root / "object-versions.jsonl"

    def object_path(self, digest: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise AcquisitionError("OBJECT_HASH_INVALID")
        return self.root / "objects" / "sha256" / digest[:2] / digest

    def read(self, digest: str) -> bytes:
        raw = self.object_path(digest).read_bytes()
        if sha(raw) != digest:
            raise AcquisitionError("IMMUTABLE_CACHE_HASH_MISMATCH")
        return raw

    def latest(self, url: str) -> dict[str, Any] | None:
        last = None
        if self.registry.exists():
            for line in self.registry.read_bytes().splitlines():
                entry = json.loads(line)
                if entry["url"] == url:
                    last = entry
        return last

    def store(self, url: str, raw: bytes, content_type: str) -> dict[str, Any]:
        digest = sha(raw)
        path = self.object_path(digest)
        try:
            write_new(path, raw)
        except FileExistsError:
            self.read(digest)
        record = {
            "url": url,
            "sha256": digest,
            "bytes": len(raw),
            "accepted_at_utc": utc_now().isoformat(),
            "content_type": content_type,
            "object_locator": str(path.relative_to(self.root)).replace("\\", "/"),
        }
        append_record(self.registry, record)
        return record


class Client:
    def __init__(
        self,
        root: Path,
        snapshot: Path,
        user_agent: str,
        scheduler: Scheduler,
        transport: Transport,
        *,
        refresh: bool = False,
        deadline: float | None = None,
        jitter: Callable[[], float] = lambda: random.uniform(0, 0.5),
    ) -> None:
        self.cache = Cache(root)
        self.snapshot = snapshot
        self.agent = user_agent
        self.scheduler = scheduler
        self.transport = transport
        self.refresh = refresh
        self.deadline = deadline if deadline is not None else scheduler.clock() + 1800
        self.jitter = jitter
        self.requests = snapshot / "requests.jsonl"
        self.failures = snapshot / "failures.jsonl"
        self.consecutive_429 = 0

    def fail(self, url: str, reason: str, attempt: int, status: int | None = None) -> None:
        append_record(
            self.failures,
            {
                "url": url,
                "reason": reason,
                "attempt": attempt,
                "status": status,
                "utc": utc_now().isoformat(),
                "deferred_reason": reason if "DEFERRED" in reason else None,
                "not_before_utc_epoch": self.scheduler.state().get("not_before"),
            },
        )

    def fetch(self, url: str) -> tuple[bytes, dict[str, Any]]:
        validate_url(url)
        if not self.refresh:
            cached = self.cache.latest(url)
            if cached:
                raw = self.cache.read(cached["sha256"])
                index_payload(raw, url)
                append_record(
                    self.requests,
                    {
                        "url": url,
                        "cache_hit": True,
                        "response_sha256": cached["sha256"],
                        "bytes": len(raw),
                        "utc": utc_now().isoformat(),
                        "attempt": 0,
                        "request_id": sha(
                            f"{self.snapshot.name}|cache|{url}|{utc_now().isoformat()}".encode()
                        ),
                        "host": urlsplit(url).hostname,
                        "path": urlsplit(url).path,
                        "status": None,
                        "headers_hash": None,
                        "delay": 0.0,
                        "deferred_reason": None,
                    },
                )
                return raw, cached
        used = self.scheduler.attempts_used(url)
        target = url
        for attempt in range(used + 1, 6):
            validate_url(target)
            try:
                started = self.scheduler.start(self.deadline)
            except AcquisitionError as error:
                self.fail(url, str(error), attempt)
                raise
            self.scheduler.record_attempt(url)
            log: dict[str, Any] = {
                "request_id": sha(f"{self.snapshot.name}|{url}|{attempt}|{started}".encode()),
                "url": target,
                "original_url": url,
                "attempt": attempt,
                "utc": datetime.fromtimestamp(started, UTC).isoformat(),
                "host": urlsplit(target).hostname,
                "path": urlsplit(target).path,
                "headers_hash": sha(self.agent.encode()),
                "cache_hit": False,
                "delay": self.scheduler.last_delay,
                "deferred_reason": None,
                "response_sha256": None,
                "bytes": 0,
            }
            try:
                response = self.transport.get(target, self.agent)
            except (OSError, http.client.HTTPException, AcquisitionError) as error:
                if isinstance(error, FirewallError):
                    reason = "LOCAL_FIREWALL_DENIAL:" + str(error)
                else:
                    reason = (
                        str(error) if isinstance(error, AcquisitionError) else "TRANSPORT_FAILURE"
                    )
                log.update(status=None, error_type=type(error).__name__, reason=reason)
                append_record(self.requests, log)
                self.fail(url, reason, attempt)
                if isinstance(error, (AcquisitionError, FirewallError)) or attempt == 5:
                    raise AcquisitionError(reason) from error
                self.scheduler.hold(2 ** (attempt - 1) + self.jitter(), reason)
                continue
            log.update(
                status=response.status,
                bytes=len(response.body),
                body_sha256=sha(response.body),
                response_sha256=sha(response.body) if response.status == 200 else None,
                retry_after_header=response.headers.get("retry-after"),
                content_type=response.headers.get("content-type", ""),
            )
            append_record(self.requests, log)
            if response.status == 403:
                self.scheduler.block("HTTP_403_STOP_NO_EVASION")
                self.fail(url, "HTTP_403_STOP_NO_EVASION", attempt, 403)
                raise AcquisitionError("HTTP_403_STOP_NO_EVASION")
            if response.status == 429:
                self.consecutive_429 += 1
            else:
                self.consecutive_429 = 0
            if response.status in {301, 302, 303, 307, 308}:
                target = urljoin(target, response.headers.get("location", ""))
                try:
                    validate_url(target)
                except AcquisitionError:
                    self.fail(url, "OFF_REGISTRY_REDIRECT", attempt, response.status)
                    raise
                self.fail(url, "REGISTERED_REDIRECT", attempt, response.status)
                continue
            if response.status in {408, 429, 500, 502, 503, 504}:
                delay = max(
                    2 ** (attempt - 1) + self.jitter(),
                    retry_after_seconds(
                        response.headers.get("retry-after"),
                        datetime.fromtimestamp(self.scheduler.clock(), UTC),
                    ),
                )
                self.scheduler.hold(delay, "HTTP_RETRY_AFTER_OR_BACKOFF")
                self.fail(url, "HTTP_TRANSIENT", attempt, response.status)
                if self.consecutive_429 >= 2:
                    raise AcquisitionError("TWO_CONSECUTIVE_429_STOP_BATCH")
                continue
            if response.status != 200:
                self.fail(url, "HTTP_PERMANENT_FAILURE", attempt, response.status)
                raise AcquisitionError("HTTP_PERMANENT_FAILURE")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            expected = (
                {"application/zip", "application/x-zip-compressed", "application/octet-stream"}
                if url.endswith(".zip")
                else {"text/plain", "application/octet-stream"}
            )
            if content_type not in expected:
                self.fail(url, "CONTENT_TYPE_INVALID", attempt, 200)
                raise AcquisitionError("CONTENT_TYPE_INVALID")
            try:
                index_payload(response.body, url)
            except AcquisitionError as error:
                self.fail(url, str(error), attempt, 200)
                raise
            record = self.cache.store(url, response.body, content_type)
            self.scheduler.record_success(url)
            return response.body, record
        self.fail(url, "MAXIMUM_FIVE_ATTEMPTS_REACHED", 5)
        raise AcquisitionError("MAXIMUM_FIVE_ATTEMPTS_REACHED")


@dataclass(frozen=True)
class IndexRow:
    cik: str
    company_name: str
    form: str
    filed: str
    filename: str

    def record_hash(self) -> bytes:
        return hashlib.sha256(
            json_bytes(
                [
                    self.cik,
                    self.company_name,
                    self.form,
                    self.filed,
                    self.filename,
                ]
            )
        ).digest()


def parse_master(content: bytes, encoding: str) -> Iterator[IndexRow]:
    found_header = False
    for number, line in enumerate(
        content.decode(encoding.removesuffix("-lossless")).splitlines(), 1
    ):
        if not found_header:
            found_header = line == "CIK|Company Name|Form Type|Date Filed|Filename"
            continue
        if not line.strip() or set(line.strip()) == {"-"}:
            continue
        fields = [value.strip() for value in line.split("|")]
        if (
            len(fields) != 5
            or not re.fullmatch(r"[0-9]{1,10}", fields[0])
            or int(fields[0]) <= 0
            or not fields[1]
            or not fields[2]
            or not re.fullmatch(
                r"edgar/data/[0-9]{1,10}/[0-9]{10}-[0-9]{2}-[0-9]{6}\.txt", fields[4]
            )
        ):
            raise AcquisitionError(f"MASTER_INDEX_ROW_INVALID_AT_LINE_{number}")
        try:
            if date.fromisoformat(fields[3]).isoformat() != fields[3]:
                raise ValueError
        except ValueError as error:
            raise AcquisitionError(f"MASTER_INDEX_DATE_INVALID_AT_LINE_{number}") from error
        yield IndexRow(*fields)
    if not found_header:
        raise AcquisitionError("MASTER_INDEX_HEADER_INVALID")


def discovery_routes(canonical: str) -> tuple[tuple[str, str], ...]:
    if canonical == "4":
        return (("INSIDER_OPEN_MARKET_PURCHASE", "CANDIDATE_SOURCE"),)
    if canonical == "4/A":
        return (("INSIDER_OPEN_MARKET_PURCHASE", "AMENDMENT_CONTEXT"),)
    if canonical == "INITIAL_13D":
        return (("ACTIVIST_13D", "CANDIDATE_SOURCE"),)
    if canonical in {"AMENDED_13D", "PASSIVE_13G", "AMENDED_13G"}:
        return (("ACTIVIST_13D", "AMENDMENT_OR_PASSIVE_CONTEXT"),)
    if canonical in {"8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A"}:
        role = "AMENDMENT_CONTEXT" if canonical.endswith("/A") else "CANDIDATE_SOURCE"
        return tuple(
            (family, role)
            for family in ("SHARE_REPURCHASE", "DIVIDEND_POSITIVE_CHANGE", "SPINOFF_ANNOUNCEMENT")
        )
    if canonical:
        return (("SPINOFF_ANNOUNCEMENT", "REGISTRATION_PROXY_PROVENANCE"),)
    return ()


SCHEMA = """
PRAGMA foreign_keys=ON;
PRAGMA temp_store=MEMORY;
PRAGMA cache_size=-262144;
PRAGMA mmap_size=1073741824;
CREATE TEMP TABLE staging (
 digest BLOB PRIMARY KEY, year INTEGER, form_raw TEXT, form_canonical TEXT,
 path_digest BLOB) WITHOUT ROWID;
CREATE TABLE seen_records (digest BLOB PRIMARY KEY) WITHOUT ROWID;
CREATE TABLE seen_filing_paths (digest BLOB PRIMARY KEY) WITHOUT ROWID;
CREATE TABLE form_counts (
 year INTEGER, form_raw TEXT, form_canonical TEXT,
 raw_rows INTEGER NOT NULL, unique_rows INTEGER NOT NULL,
 PRIMARY KEY(year,form_raw));
CREATE TABLE route_counts (
 year INTEGER, family TEXT, route_role TEXT, form_raw TEXT, records INTEGER NOT NULL,
 PRIMARY KEY(year,family,route_role,form_raw));
CREATE TABLE index_files (
 year INTEGER, quarter INTEGER, url TEXT, raw_sha256 TEXT, index_sha256 TEXT,
 raw_rows INTEGER, unique_rows INTEGER, duplicates INTEGER, outside_range INTEGER,
 registered_records INTEGER, unique_filing_paths INTEGER, encoding TEXT,
 PRIMARY KEY(year,quarter));
"""


def sqlite_authorizer(
    action: int,
    first: str | None,
    second: str | None,
    database: str | None,
    trigger: str | None,
) -> int:
    if action in {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (second or "").lower() == "load_extension":
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def ingest_index(
    db: sqlite3.Connection,
    year: int,
    quarter: int,
    url: str,
    raw: bytes,
    record: dict[str, Any],
    aliases: dict[str, str],
) -> dict[str, int]:
    """Same five-field identity as v1; batched SQL changes throughput only."""
    content, encoding = index_payload(raw, url)
    totals = dict(
        raw_rows=0,
        unique_rows=0,
        duplicates=0,
        outside_range=0,
        registered_records=0,
        unique_filing_paths=0,
    )
    raw_form: dict[tuple[int, str, str], int] = {}
    routes: dict[tuple[int, str, str, str], int] = {}
    batch: list[tuple[bytes, int, str, str, bytes]] = []
    with db:
        db.execute("DELETE FROM staging")
        for row in parse_master(content, encoding):
            totals["raw_rows"] += 1
            if not "2006-01-01" <= row.filed <= "2024-12-31":
                totals["outside_range"] += 1
                continue
            filed_year = int(row.filed[:4])
            canonical = aliases.get(row.form.upper(), "")
            key = filed_year, row.form, canonical
            raw_form[key] = raw_form.get(key, 0) + 1
            batch.append(
                (
                    row.record_hash(),
                    filed_year,
                    row.form,
                    canonical,
                    hashlib.sha256(row.filename.encode()).digest(),
                )
            )
            if len(batch) == 10000:
                db.executemany("INSERT OR IGNORE INTO staging VALUES (?,?,?,?,?)", batch)
                batch.clear()
        if batch:
            db.executemany("INSERT OR IGNORE INTO staging VALUES (?,?,?,?,?)", batch)
        if totals["raw_rows"] == 0:
            raise AcquisitionError("MASTER_INDEX_HAS_NO_RECORDS")
        new_form = {
            (row[0], row[1], row[2]): row[3]
            for row in db.execute(
                "SELECT s.year,s.form_raw,s.form_canonical,count(*) "
                "FROM staging s LEFT JOIN seen_records v ON v.digest=s.digest "
                "WHERE v.digest IS NULL GROUP BY s.year,s.form_raw,s.form_canonical"
            )
        }
        totals["unique_rows"] = sum(new_form.values())
        totals["duplicates"] = totals["raw_rows"] - totals["outside_range"] - totals["unique_rows"]
        totals["registered_records"] = sum(
            count for (_, _, canonical), count in new_form.items() if canonical
        )
        totals["unique_filing_paths"] = db.execute(
            "INSERT OR IGNORE INTO seen_filing_paths "
            "SELECT DISTINCT s.path_digest FROM staging s "
            "LEFT JOIN seen_records v ON v.digest=s.digest "
            "WHERE v.digest IS NULL ORDER BY s.path_digest"
        ).rowcount
        inserted = db.execute(
            "INSERT OR IGNORE INTO seen_records SELECT digest FROM staging ORDER BY digest"
        ).rowcount
        if inserted != totals["unique_rows"]:
            raise AcquisitionError("BATCH_DEDUP_RECONCILIATION_FAILURE")
        db.executemany(
            "INSERT INTO form_counts VALUES (?,?,?,?,?) "
            "ON CONFLICT(year,form_raw) DO UPDATE SET "
            "raw_rows=raw_rows+excluded.raw_rows,unique_rows=unique_rows+excluded.unique_rows",
            [(*key, count, new_form.get(key, 0)) for key, count in raw_form.items()],
        )
        for (filed_year, form, canonical), count in new_form.items():
            for family, role in discovery_routes(canonical):
                key_route = filed_year, family, role, form
                routes[key_route] = routes.get(key_route, 0) + count
        db.executemany(
            "INSERT INTO route_counts VALUES (?,?,?,?,?) "
            "ON CONFLICT(year,family,route_role,form_raw) "
            "DO UPDATE SET records=records+excluded.records",
            [(*key, count) for key, count in routes.items()],
        )
        db.execute(
            "INSERT INTO index_files VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (year, quarter, url, record["sha256"], sha(content), *totals.values(), encoding),
        )
    return totals


def export_census(db: sqlite3.Connection, snapshot: Path, complete: bool) -> dict[str, Any]:
    forms = [
        dict(zip(("year", "form", "canonical_form", "raw_rows", "unique_rows"), row, strict=True))
        for row in db.execute("SELECT * FROM form_counts ORDER BY year,form_raw")
    ]
    routes = [
        dict(zip(("year", "family", "route_role", "form", "records"), row, strict=True))
        for row in db.execute("SELECT * FROM route_counts ORDER BY year,family,route_role,form_raw")
    ]
    indices = db.execute("SELECT count(*) FROM index_files").fetchone()[0]
    totals = {
        name: db.execute(f"SELECT coalesce(sum({name}),0) FROM index_files").fetchone()[0]
        for name in (
            "raw_rows",
            "unique_rows",
            "duplicates",
            "outside_range",
            "registered_records",
            "unique_filing_paths",
        )
    }
    if totals["unique_rows"] != sum(row["unique_rows"] for row in forms):
        raise AcquisitionError("CENSUS_RECONCILIATION_FAILURE")
    coverage = [
        {
            "year": year,
            "planned_indices": 4,
            "completed_indices": db.execute(
                "SELECT count(*) FROM index_files WHERE year=?", (year,)
            ).fetchone()[0],
        }
        for year in range(2006, 2025)
    ]
    summary = {
        "status": "COMPLETE_METADATA_CENSUS" if complete else "INCOMPLETE_METADATA_CENSUS",
        "planned_indices": 76,
        "completed_indices": indices,
        "complete_range_totals": totals if complete else None,
        "observed_partial_totals": totals,
        "year_coverage": coverage,
        "year_form_counts": forms,
        "year_family_routes": routes,
        "family_route_counts_are_nonadditive": True,
        "counts_are_source_records_not_events": True,
        "date_filed_is_not_public_availability": True,
        "dedup_key": "SHA256 canonical JSON of all five normalized master-index fields",
        "unique_filing_paths_are_separate_from_source_records": True,
        "no_current_ticker_seed": True,
        "filing_bodies_downloaded": 0,
        "parser_QA_ready_to_begin": complete,
        "firewall_counters": dict.fromkeys(FIREWALL_COUNTERS, 0),
    }
    write_new(snapshot / "census-summary.json", json_bytes(summary))
    for filename, records, fields in (
        (
            "year-form-counts.csv",
            forms,
            ["year", "form", "canonical_form", "raw_rows", "unique_rows"],
        ),
        (
            "year-family-route-counts.csv",
            routes,
            ["year", "family", "route_role", "form", "records"],
        ),
    ):
        # Values are JSON-quoted strings: valid CSV quoting doubles embedded quotes.
        lines = [",".join(fields)]
        for item in records:
            cells = []
            for field in fields:
                value = str(item[field])
                cells.append('"' + value.replace('"', '""') + '"')
            lines.append(",".join(cells))
        write_new(snapshot / filename, ("\n".join(lines) + "\n").encode())
    return summary


def run_census(
    snapshot: Path,
    client: Client,
    aliases: dict[str, str],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    write_new(snapshot / "index-plan.json", json_bytes(index_plan()))
    write_new(snapshot / "preflight.json", json_bytes(provenance))
    for name in ("requests.jsonl", "failures.jsonl", "source-objects.jsonl"):
        write_new(snapshot / name, b"")
    db_path = snapshot / "metadata-census.sqlite3"
    blocker = None
    with sqlite3.connect(db_path) as db:
        db.executescript(SCHEMA)
        db.set_authorizer(sqlite_authorizer)
        for year, quarter, url in index_plan():
            try:
                raw, record = client.fetch(url)
                totals = ingest_index(db, year, quarter, url, raw, record, aliases)
                append_record(
                    snapshot / "source-objects.jsonl",
                    {
                        "year": year,
                        "quarter": quarter,
                        **record,
                    },
                )
                print(
                    json.dumps(
                        {
                            "index_completed": f"{year}Q{quarter}",
                            "source_rows": totals["raw_rows"],
                            "registered_source_rows": totals["registered_records"],
                        }
                    ),
                    flush=True,
                )
            except (AcquisitionError, OSError, sqlite3.DatabaseError) as error:
                blocker = (
                    str(error) if isinstance(error, AcquisitionError) else type(error).__name__
                )
                client.fail(url, blocker, 0)
                break
        completed = db.execute("SELECT count(*) FROM index_files").fetchone()[0] == 76
        summary = export_census(db, snapshot, completed and blocker is None)
    db.close()
    files = [
        "index-plan.json",
        "preflight.json",
        "requests.jsonl",
        "failures.jsonl",
        "source-objects.jsonl",
        "metadata-census.sqlite3",
        "census-summary.json",
        "year-form-counts.csv",
        "year-family-route-counts.csv",
    ]
    authority = {
        "snapshot_id": snapshot.name,
        "kind": "SEC_HISTORICAL_INDEX_METADATA_ONLY",
        "created_at_utc": utc_now().isoformat(),
        "event_range": ["2006-01-01", "2024-12-31"],
        "protocol": provenance,
        "status": summary["status"],
        "blocker": blocker,
        "raw_source_versions": "source-objects.jsonl",
        "files": {name: sha((snapshot / name).read_bytes()) for name in files},
        "runtime": {"python": sys.version.split()[0], "dependencies": "Python standard library"},
        "request_control": {
            "global_ceiling_rps": 2,
            "minimum_start_spacing_seconds": 0.5,
            "concurrency": 1,
            "max_attempts": 5,
            "timeout_seconds": 30,
            "single_owner": True,
            "other_clients_or_machines_for_this_identity": "FORBIDDEN",
            "coordination_scope": "This workflow's fixed scheduler lock; no distributed broker",
            "contact_value_logged": False,
            "block_evasion": False,
        },
        "firewall_counters": dict.fromkeys(FIREWALL_COUNTERS, 0),
    }
    write_new(snapshot / "authority-manifest.json", json_bytes(authority))
    return {
        "status": summary["status"],
        "blocker": blocker,
        "snapshot_id": snapshot.name,
        "completed_indices": summary["completed_indices"],
        "manifest_sha256": sha((snapshot / "authority-manifest.json").read_bytes()),
        "parser_QA_ready_to_begin": summary["parser_QA_ready_to_begin"],
    }


def main() -> int:
    if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode:
        print("Use python -I -S -B tools/corporate_events/sec_census.py", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "census"))
    parser.add_argument("--snapshot", default="")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--max-seconds", type=int, default=1800)
    args = parser.parse_args()
    try:
        provenance = preflight()
        contact = private_contact(ROOT)
        if args.command == "validate":
            print(
                json.dumps(
                    {
                        "protocol": "PASS",
                        "contact": "CONFIGURED_PRIVATE",
                        "planned_indices": len(index_plan()),
                        "scope": "METADATA_ONLY",
                        "network_requests": 0,
                        "firewall_counters": dict.fromkeys(FIREWALL_COUNTERS, 0),
                    }
                )
            )
            return 0
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", args.snapshot)
            or args.snapshot in {".", ".."}
            or not 1 <= args.max_seconds <= 3600
        ):
            raise AcquisitionError("SNAPSHOT_OR_DEADLINE_INVALID")
        if PRIVATE.resolve() != PRIVATE.absolute():
            raise AcquisitionError("PRIVATE_PATH_ALIAS_DENIED")
        transport = (
            HttpsTransport()
        )  # Public TLS trust initialization before capability restriction.
        protocol = tomllib.loads((PROTOCOL / "event-data-protocol.toml").read_text("utf-8"))
        aliases = dict(protocol["source_registry"]["form_aliases"])
        PRIVATE.mkdir(parents=True, exist_ok=True)
        snapshots = PRIVATE / "snapshots"
        snapshots.mkdir(exist_ok=True)
        snapshot = snapshots / args.snapshot
        if snapshot.exists():
            raise AcquisitionError("IMMUTABLE_SNAPSHOT_ALREADY_EXISTS")
        user_agent = "CorporateEventAlphaResearch/phase2-acquisition-v1 (" + contact + ")"
        install_firewall()
        with Scheduler(PRIVATE) as scheduler:
            snapshot.mkdir()
            client = Client(
                PRIVATE,
                snapshot,
                user_agent,
                scheduler,
                transport,
                refresh=args.refresh,
                deadline=time.time() + args.max_seconds,
            )
            outcome = run_census(snapshot, client, aliases, provenance)
        print(json.dumps(outcome), flush=True)
        return 0 if outcome["status"] == "COMPLETE_METADATA_CENSUS" else 3
    except (AcquisitionError, OSError, subprocess.CalledProcessError) as error:
        message = str(error) if isinstance(error, AcquisitionError) else type(error).__name__
        print(json.dumps({"status": "STOPPED", "reason": message}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
