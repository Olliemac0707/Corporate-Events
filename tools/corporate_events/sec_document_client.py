"""Bounded SEC complete-submission access using the existing global scheduler."""

from __future__ import annotations

import http.client
import json
import random
import re
import ssl
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

CATALOGUE = "https://www.sec.gov/submit-filings/technical-specifications"


def validate_url(url: str, allowed: set[str]) -> tuple[str, str]:
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
        raise ValueError("DOCUMENT_URL_OUTSIDE_REGISTRY")
    canonical = "https://www.sec.gov" + parsed.path
    if canonical not in allowed:
        raise ValueError("DOCUMENT_NOT_IN_FROZEN_PILOT_PLAN")
    if canonical != CATALOGUE and not re.fullmatch(
        r"/Archives/edgar/data/[0-9]{1,10}/[0-9]{10}-[0-9]{2}-[0-9]{6}\.txt", parsed.path
    ):
        raise ValueError("DOCUMENT_PATH_OUTSIDE_PILOT_CAPABILITY")
    return parsed.hostname, parsed.path


class ByteBudget:
    def __init__(self, path: Path, limit: int, plan_sha256: str) -> None:
        self.path, self.limit, self.plan_sha256 = path, limit, plan_sha256
        if path.exists():
            saved = json.loads(path.read_bytes())
            if saved["plan_sha256"] != plan_sha256 or saved["limit"] != limit:
                raise ValueError("PILOT_DOWNLOAD_BUDGET_IDENTITY_MISMATCH")
            self.spent = int(saved["received_body_bytes"])
            if not 0 <= self.spent <= limit:
                raise ValueError("PILOT_DOWNLOAD_BUDGET_STATE_INVALID")
        else:
            self.spent = 0

    def cap(self) -> int:
        remaining = self.limit - self.spent
        if remaining <= 1:
            raise ValueError("PILOT_DOWNLOAD_BYTE_BUDGET_EXHAUSTED")
        return min(52_428_800, remaining) - 1

    def charge(self, count: int) -> None:
        if count < 0 or self.spent + count > self.limit:
            raise ValueError("PILOT_BUDGET_INVARIANT_FAILURE")
        self.spent += count
        self.path.write_text(
            json.dumps(
                {
                    "plan_sha256": self.plan_sha256,
                    "limit": self.limit,
                    "received_body_bytes": self.spent,
                }
            )
            + "\n",
            encoding="utf-8",
        )


class HttpsDocuments:
    def __init__(
        self, sec: Any, allowed: set[str], context: ssl.SSLContext, budget: ByteBudget
    ) -> None:
        self.sec, self.allowed, self.context, self.budget = sec, allowed, context, budget
        self.last_bytes = 0
        self.last_sha: str | None = None

    def get(self, url: str, agent: str) -> Any:
        self.last_bytes, self.last_sha = 0, None
        host, path = validate_url(url, self.allowed)
        cap = self.budget.cap()
        token = self.sec.NETWORK_HOST.set(host)
        connection = http.client.HTTPSConnection(host, 443, timeout=30, context=self.context)
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "User-Agent": agent,
                    "Accept": "text/plain,text/html,application/xml,*/*",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            reply = connection.getresponse()
            headers = {key.lower(): val for key, val in reply.getheaders()}
            if reply.status != 200:
                return self.sec.Response(reply.status, headers, b"")
            length = headers.get("content-length")
            if length and (not length.isdigit() or int(length) > cap):
                raise self.sec.AcquisitionError("DOCUMENT_LENGTH_OVER_RESPONSE_OR_PILOT_BOUND")
            chunks = []
            while self.last_bytes <= cap:
                try:
                    chunk = reply.read(min(65536, cap + 1 - self.last_bytes))
                except http.client.IncompleteRead as error:
                    self.budget.charge(len(error.partial))
                    self.last_bytes += len(error.partial)
                    raise
                if not chunk:
                    break
                self.budget.charge(len(chunk))
                self.last_bytes += len(chunk)
                chunks.append(chunk)
            body = b"".join(chunks)
            self.last_sha = self.sec.sha(body)
            if len(body) > cap:
                raise self.sec.AcquisitionError("DOCUMENT_RESPONSE_TOO_LARGE")
            if length and len(body) != int(length):
                raise self.sec.AcquisitionError("DOCUMENT_LENGTH_MISMATCH")
            return self.sec.Response(reply.status, headers, body)
        finally:
            connection.close()
            self.sec.NETWORK_HOST.reset(token)


def validate_payload(raw: bytes, url: str) -> None:
    beginning = raw[:16384].lower()
    submission_header = re.search(rb"(?im)^<sec-document>|^<sec-header>", beginning)
    if not submission_header and (
        b"your request originates from an undeclared automated tool" in beginning
        or re.search(
            rb"<title>[^<]*(?:request rate threshold|access denied|"
            rb"undeclared automated)[^<]*</title>",
            beginning,
        )
    ):
        raise ValueError("SEC_BLOCKED_PAGE_STOP_REVIEW")
    if urlsplit(url).path == urlsplit(CATALOGUE).path:
        if b"<html" not in raw[:10000].lower() or b"specification" not in raw.lower():
            raise ValueError("TECHNICAL_CATALOGUE_STRUCTURE_INVALID")
    elif not re.search(rb"(?im)^<SEC-DOCUMENT>|^<SEC-HEADER>", raw[:65536]):
        raise ValueError("COMPLETE_SUBMISSION_STRUCTURE_INVALID")


class DocumentClient:
    def __init__(
        self,
        sec: Any,
        root: Path,
        snapshot: Path,
        scheduler: Any,
        transport: Any,
        allowed: set[str],
        agent: str,
        deadline: float,
    ) -> None:
        self.sec, self.root, self.snapshot, self.scheduler = sec, root, snapshot, scheduler
        self.transport, self.allowed, self.agent, self.deadline = (
            transport,
            allowed,
            agent,
            deadline,
        )
        self.cache = sec.Cache(root)
        self.consecutive_429 = 0

    def fail(self, url: str, reason: str, attempt: int, status: int | None = None) -> None:
        self.sec.append_record(
            self.snapshot / "failures.jsonl",
            {
                "url": url,
                "reason": reason,
                "attempt": attempt,
                "status": status,
                "utc": self.sec.utc_now().isoformat(),
                "not_before_utc_epoch": self.scheduler.state().get("not_before"),
            },
        )

    def fetch(self, url: str) -> tuple[bytes, dict[str, Any]]:
        validate_url(url, self.allowed)
        cached = self.cache.latest(url)
        if cached:
            raw = self.cache.read(cached["sha256"])
            validate_payload(raw, url)
            self.sec.append_record(
                self.snapshot / "requests.jsonl",
                {
                    "request_id": self.sec.sha(
                        f"{self.snapshot.name}|cache|{url}|{time.time()}".encode()
                    ),
                    "url": url,
                    "host": urlsplit(url).hostname,
                    "path": urlsplit(url).path,
                    "utc": self.sec.utc_now().isoformat(),
                    "attempt": 0,
                    "status": None,
                    "headers_hash": None,
                    "response_sha256": cached["sha256"],
                    "bytes": len(raw),
                    "cache_hit": True,
                    "delay": 0.0,
                    "deferred_reason": None,
                },
            )
            return raw, cached
        target = url
        used = self.scheduler.attempts_used(url)
        if not 0 <= used <= 5:
            raise self.sec.AcquisitionError("DOCUMENT_ATTEMPT_BUDGET_STATE_INVALID")
        for attempt in range(used + 1, 6):
            started = self.scheduler.start(self.deadline)
            self.scheduler.record_attempt(url)
            row: dict[str, Any] = {
                "request_id": self.sec.sha(
                    f"{self.snapshot.name}|{url}|{attempt}|{started}".encode()
                ),
                "url": target,
                "original_url": url,
                "host": urlsplit(target).hostname,
                "path": urlsplit(target).path,
                "utc": datetime.fromtimestamp(started, UTC).isoformat(),
                "attempt": attempt,
                "headers_hash": self.sec.sha(self.agent.encode()),
                "cache_hit": False,
                "delay": self.scheduler.last_delay,
                "deferred_reason": None,
                "response_sha256": None,
                "bytes": 0,
            }
            try:
                response = self.transport.get(target, self.agent)
            except (
                OSError,
                http.client.HTTPException,
                ValueError,
                self.sec.AcquisitionError,
            ) as error:
                local = isinstance(error, self.sec.FirewallError)
                reason = (
                    "LOCAL_FIREWALL_DENIAL:" + str(error)
                    if local
                    else str(error)
                    if isinstance(error, (ValueError, self.sec.AcquisitionError))
                    else "DOCUMENT_TRANSPORT_FAILURE"
                )
                row.update(
                    status=None,
                    reason=reason,
                    error_type=type(error).__name__,
                    bytes=getattr(self.transport, "last_bytes", 0),
                    body_sha256=getattr(self.transport, "last_sha", None),
                )
                self.sec.append_record(self.snapshot / "requests.jsonl", row)
                self.fail(url, reason, attempt)
                if (
                    local
                    or isinstance(error, (ValueError, self.sec.AcquisitionError))
                    or attempt == 5
                ):
                    raise self.sec.AcquisitionError(reason) from error
                self.scheduler.hold(2 ** (attempt - 1) + random.random() / 2, reason)
                continue
            row.update(
                status=response.status,
                bytes=len(response.body),
                body_sha256=self.sec.sha(response.body),
                response_sha256=self.sec.sha(response.body) if response.status == 200 else None,
                content_type=response.headers.get("content-type", ""),
                retry_after_header=response.headers.get("retry-after"),
            )
            self.sec.append_record(self.snapshot / "requests.jsonl", row)
            if response.status == 403:
                self.scheduler.block("HTTP_403_STOP_NO_EVASION")
                self.fail(url, "HTTP_403_STOP_NO_EVASION", attempt, 403)
                raise self.sec.AcquisitionError("HTTP_403_STOP_NO_EVASION")
            self.consecutive_429 = self.consecutive_429 + 1 if response.status == 429 else 0
            if response.status in {301, 302, 303, 307, 308}:
                redirected = urljoin(target, response.headers.get("location", ""))
                try:
                    if validate_url(redirected, self.allowed) != validate_url(url, self.allowed):
                        raise ValueError("DOCUMENT_REDIRECT_IDENTITY_MISMATCH")
                except ValueError as error:
                    self.fail(url, str(error), attempt, response.status)
                    raise self.sec.AcquisitionError(str(error)) from error
                target = redirected
                continue
            if response.status in {408, 429, 500, 502, 503, 504}:
                delay = max(
                    2 ** (attempt - 1) + random.random() / 2,
                    self.sec.retry_after_seconds(
                        response.headers.get("retry-after"),
                        datetime.fromtimestamp(self.scheduler.clock(), UTC),
                    ),
                )
                self.scheduler.hold(delay, "DOCUMENT_RETRY_AFTER_OR_BACKOFF")
                self.fail(url, "DOCUMENT_TRANSIENT_HTTP", attempt, response.status)
                if self.consecutive_429 >= 2:
                    raise self.sec.AcquisitionError("TWO_CONSECUTIVE_429_STOP_BATCH")
                continue
            if response.status != 200:
                self.fail(url, "DOCUMENT_HTTP_PERMANENT_FAILURE", attempt, response.status)
                raise self.sec.AcquisitionError("DOCUMENT_HTTP_PERMANENT_FAILURE")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type not in {
                "text/plain",
                "text/html",
                "application/octet-stream",
                "application/xml",
                "text/xml",
            }:
                raise self.sec.AcquisitionError("DOCUMENT_CONTENT_TYPE_INVALID")
            try:
                validate_payload(response.body, url)
            except ValueError as error:
                if str(error) == "SEC_BLOCKED_PAGE_STOP_REVIEW":
                    self.scheduler.block(str(error))
                self.fail(url, str(error), attempt, response.status)
                raise self.sec.AcquisitionError(str(error)) from error
            record = self.cache.store(url, response.body, content_type)
            self.scheduler.record_success(url)
            return response.body, record
        raise self.sec.AcquisitionError("MAXIMUM_FIVE_ATTEMPTS_REACHED")
