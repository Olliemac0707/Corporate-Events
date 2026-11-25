"""Bounded SEC search metadata transport; no filing-body or market capability."""

from __future__ import annotations

import calendar
import http.client
import json
import re
import ssl
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

PAGE = "https://www.sec.gov/edgar/search/"
SEARCH = "https://efts.sec.gov/LATEST/search-index"
MAX_RESPONSE = 2_097_152


def validate_request(url: str, method: str, payload: dict[str, Any] | None) -> None:
    p = urlsplit(url)
    if (
        p.scheme != "https"
        or p.username
        or p.password
        or p.port not in {None, 443}
        or p.query
        or p.fragment
        or ".." in p.path
        or "%" in p.path
    ):
        raise ValueError("SEARCH_REQUEST_OUTSIDE_CAPABILITY")
    script = bool(
        re.fullmatch(r"https://www\.sec\.gov/edgar/search/(?:js/)?[A-Za-z0-9_-]+\.js", url)
    )
    if url == PAGE or script:
        if method != "GET" or payload is not None:
            raise ValueError("SEARCH_AUTHORITY_GET_ONLY")
    elif url == SEARCH:
        if method != "GET" or not isinstance(payload, dict):
            raise ValueError("SEARCH_METADATA_GET_ONLY")
        validate_payload(payload)
    else:
        raise ValueError("SEARCH_REQUEST_OUTSIDE_CAPABILITY")


QUERY = 'repurchase OR buyback OR "buy back"'


def search_payload(year: int, month: int, offset: int = 0) -> dict[str, Any]:
    if type(year) is not int or not 2006 <= year <= 2015:
        raise ValueError("SEARCH_YEAR_OUTSIDE_DEVELOPMENT")
    if type(month) is not int or not 1 <= month <= 12:
        raise ValueError("SEARCH_MONTH_INVALID")
    if type(offset) is not int or offset < 0 or offset >= 10000 or offset % 100:
        raise ValueError("SEARCH_OFFSET_INVALID")
    return {
        "q": QUERY,
        "dateRange": "custom",
        "category": "custom",
        "startdt": f"{year}-{month:02d}-01",
        "enddt": f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}",
        "forms": "8-K",
        "from": offset,
        "size": 100,
    }


def validate_payload(payload: dict[str, Any]) -> None:
    value = payload.get("startdt")
    if not isinstance(value, str):
        raise ValueError("SEARCH_QUERY_SCOPE_INVALID")
    start = date.fromisoformat(value)
    expected = search_payload(start.year, start.month, payload.get("from", -1))
    if payload != expected or type(payload.get("size")) is not int:
        raise ValueError("SEARCH_QUERY_SCOPE_INVALID")


class Client:
    """One owner, immutable raw objects, persistent independent stage budgets."""

    def __init__(
        self,
        sec: Any,
        root: Path,
        scheduler: Any,
        agent: str,
        phase: str,
        request_cap: int,
        byte_cap: int,
        transport: Any = None,
    ) -> None:
        self.sec, self.root, self.scheduler = sec, root, scheduler
        self.agent, self.phase = agent, phase
        self.request_cap, self.byte_cap = request_cap, byte_cap
        self.transport = transport
        self.context = ssl.create_default_context()
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.consecutive_429 = 0
        self.cache = sec.Cache(root)
        self.state_path = root / (phase + "-budget.json")
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_bytes())
            if (
                self.state["request_cap"] != request_cap
                or self.state["byte_cap"] != byte_cap
                or self.state["phase"] != phase
            ):
                raise ValueError("SEARCH_BUDGET_IDENTITY_MISMATCH")
        else:
            self.state = {
                "phase": phase,
                "requests": 0,
                "bytes": 0,
                "request_cap": request_cap,
                "byte_cap": byte_cap,
                "consecutive_429": 0,
            }
        if (
            not 0 <= self.state["requests"] <= request_cap
            or not 0 <= self.state["bytes"] <= byte_cap
        ):
            raise ValueError("SEARCH_BUDGET_STATE_INVALID")
        self.consecutive_429 = int(self.state.get("consecutive_429", 0))
        if self.consecutive_429 >= 2:
            raise ValueError("TWO_CONSECUTIVE_429_STOP_BATCH")

    def save(self) -> None:
        self.state_path.write_bytes(self.sec.json_bytes(self.state))

    def exchange(self, url: str, method: str, payload: dict[str, Any] | None) -> Any:
        if self.transport is not None:
            return self.transport(url, method, payload, self.agent)
        p = urlsplit(url)
        if p.hostname is None:
            raise ValueError("SEARCH_HOST_REQUIRED")
        token = self.sec.NETWORK_HOST.set(p.hostname)
        connection = http.client.HTTPSConnection(p.hostname, 443, timeout=30, context=self.context)
        try:
            body = None
            request_path = p.path + ("?" + urlencode(payload) if payload is not None else "")
            headers = {
                "User-Agent": self.agent,
                "Accept": "application/json,text/html,text/javascript,*/*",
                "Accept-Encoding": "identity",
                "Connection": "close",
            }
            if body is not None:
                headers["Content-Type"] = "application/json"
                headers["Origin"] = "https://www.sec.gov"
            connection.request(method, request_path, body=body, headers=headers)
            response = connection.getresponse()
            hs = {k.lower(): v for k, v in response.getheaders()}
            if response.status != 200:
                return self.sec.Response(response.status, hs, b"")
            remaining = self.byte_cap - self.state["bytes"]
            limit = min(MAX_RESPONSE, remaining - 1)
            length = hs.get("content-length")
            if limit < 1 or (length is not None and (not length.isdigit() or int(length) > limit)):
                raise ValueError("SEARCH_RESPONSE_LENGTH_BUDGET")
            raw = bytearray()
            while len(raw) <= limit:
                chunk = response.read(min(65536, limit + 1 - len(raw)))
                if not chunk:
                    break
                # The probe byte is charged; never exceed the stage bound.
                if self.state["bytes"] + len(chunk) > self.byte_cap:
                    raise ValueError("SEARCH_BYTE_BUDGET_EXHAUSTED")
                self.state["bytes"] += len(chunk)
                self.save()
                raw.extend(chunk)
            if len(raw) > limit or (length is not None and len(raw) != int(length)):
                raise ValueError("SEARCH_RESPONSE_LENGTH_INVALID")
            return self.sec.Response(response.status, hs, bytes(raw))
        finally:
            connection.close()
            self.sec.NETWORK_HOST.reset(token)

    def fetch(self, url: str, method: str = "GET", payload: dict[str, Any] | None = None) -> bytes:
        validate_request(url, method, payload)
        request_key = self.sec.sha(
            self.sec.json_bytes({"method": method, "url": url, "payload": payload})
        )
        cache_key = "search-metadata:" + request_key
        cached = self.cache.latest(cache_key)
        if cached is not None:
            return bytes(self.cache.read(cached["sha256"]))
        for _ in range(5):
            limits = {
                "root-profile-diagnostic": (1, 8388608),
                "crawl-v2": (4000, 268435456),
            }
            if limits.get(self.phase) != (self.request_cap, self.byte_cap):
                raise ValueError("PAGED_SEARCH_BUDGET_PROFILE_INVALID")
            if self.phase == "root-profile-diagnostic":
                total = self.state["requests"]
                for prior_name in ("diagnostic", "exports"):
                    prior_path = self.root / (prior_name + "-budget.json")
                    if prior_path.exists():
                        total += int(json.loads(prior_path.read_bytes())["requests"])
                if total >= 12:
                    raise ValueError("COMBINED_TWELVE_DIAGNOSTIC_REQUEST_CAP")
            if self.state["requests"] >= self.request_cap:
                raise ValueError("SEARCH_REQUEST_BUDGET_EXHAUSTED")
            if self.scheduler.attempts_used(url) >= 5:
                raise ValueError("SEARCH_PER_REQUEST_ATTEMPT_CAP")
            started = self.scheduler.start(time.time() + 300)
            self.scheduler.record_attempt(url)
            self.state["requests"] += 1
            self.save()
            request = {
                "url": url,
                "method": method,
                "payload": payload,
                "request_key": request_key,
                "request_start_epoch": started,
                "agent_sha256": self.sec.sha(self.agent.encode()),
                "phase": self.phase,
            }
            self.sec.append_record(self.root / "request-plan.jsonl", request)
            try:
                response = self.exchange(url, method, payload)
                if self.transport is not None:
                    if len(response.body) > MAX_RESPONSE:
                        raise ValueError("SEARCH_RESPONSE_TOO_LARGE")
                    self.state["bytes"] += len(response.body)
                    self.save()
                    if self.state["bytes"] > self.byte_cap:
                        raise ValueError("SEARCH_BYTE_BUDGET_EXHAUSTED")
                receipt = {
                    **request,
                    "status": response.status,
                    "received_bytes": len(response.body),
                    "body_sha256": self.sec.sha(response.body),
                    "retry_after": response.headers.get("retry-after"),
                    "response_headers": {
                        k: v
                        for k, v in response.headers.items()
                        if k
                        in {"content-type", "content-length", "location", "etag", "last-modified"}
                    },
                }
                self.sec.append_record(self.root / "requests.jsonl", receipt)
                if response.status == 403:
                    self.scheduler.block("HTTP_403_STOP_NO_EVASION")
                    raise ValueError("HTTP_403_STOP_NO_EVASION")
                self.consecutive_429 = self.consecutive_429 + 1 if response.status == 429 else 0
                self.state["consecutive_429"] = self.consecutive_429
                self.save()
                if response.status in {408, 429, 500, 502, 503, 504}:
                    delay = max(
                        2.0,
                        self.sec.retry_after_seconds(
                            response.headers.get("retry-after"), self.sec.utc_now()
                        ),
                    )
                    self.scheduler.hold(delay, "SEARCH_RETRY_AFTER")
                    if self.consecutive_429 >= 2:
                        raise ValueError("TWO_CONSECUTIVE_429_STOP_BATCH")
                    continue
                if response.status != 200:
                    raise ValueError("SEARCH_HTTP_STOP_" + str(response.status))
                content_type = response.headers.get("content-type", "").split(";", 1)[0]
                allowed_types = (
                    {"application/json"}
                    if url == SEARCH
                    else {"text/html"}
                    if url == PAGE
                    else {"application/javascript", "text/javascript", "text/plain"}
                )
                if content_type not in allowed_types:
                    raise ValueError("SEARCH_CONTENT_TYPE_INVALID")
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ValueError("SEARCH_CONTENT_ENCODING_INVALID")
                beginning = response.body[:16384].lower()
                if (
                    b"your request originates from an undeclared automated tool" in beginning
                    or b"<title>access denied" in beginning
                    or b"request rate threshold exceeded" in beginning
                ):
                    self.scheduler.block("SEARCH_BLOCK_PAGE_STOP")
                    raise ValueError("SEARCH_BLOCK_PAGE_STOP")
                if url == SEARCH and not isinstance(json.loads(response.body), dict):
                    raise ValueError("SEARCH_JSON_OBJECT_REQUIRED")
                record = self.cache.store(
                    cache_key, response.body, response.headers.get("content-type", "")
                )
                self.sec.append_record(
                    self.root / "retrieval-provenance.jsonl",
                    {**request, "sha256": record["sha256"], "bytes": len(response.body)},
                )
                self.scheduler.record_success(url)
                return bytes(response.body)
            except (OSError, http.client.HTTPException, ValueError) as error:
                self.sec.append_record(
                    self.root / "failures.jsonl",
                    {
                        **request,
                        "reason_type": type(error).__name__,
                        "safe_reason": str(error)
                        if isinstance(error, ValueError)
                        else "TRANSPORT_ERROR",
                    },
                )
                raise
        raise ValueError("SEARCH_RETRY_LIMIT")
