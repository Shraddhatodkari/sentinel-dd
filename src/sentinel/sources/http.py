"""Resilient HTTP client for external sources.

Timeouts, bounded retries with exponential backoff + jitter (honouring ``Retry-After``), per-host minimum
request interval (fair-access policies), per-host circuit breakers and metrics. Failures are raised as typed
exceptions — never swallowed — so callers can record degraded coverage explicitly.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from sentinel.observability.logging import get_logger, log_event
from sentinel.observability.metrics import METRICS
from sentinel.reliability import CircuitBreaker, MinIntervalLimiter

log = get_logger(__name__)

# Published fair-access limits (seconds between requests per host)
DEFAULT_HOST_INTERVALS: dict[str, float] = {
    "www.sec.gov": 0.12,  # SEC: max 10 requests/second
    "data.sec.gov": 0.12,
    "efts.sec.gov": 0.12,
    "api.gdeltproject.org": 5.5,  # GDELT DOC API: one request every 5 seconds
    "api.gleif.org": 0.1,
    "www.justice.gov": 0.5,
}
RETRYABLE = {408, 425, 429, 500, 502, 503, 504}


class SourceError(Exception):
    def __init__(self, url: str, reason: str, status: int | None = None) -> None:
        super().__init__(f"{reason} ({status}) for {url}" if status else f"{reason} for {url}")
        self.url = url
        self.reason = reason
        self.status = status


class SourceUnavailable(SourceError):
    """Transient failure after retries, open circuit, or timeout."""


class SourceNotFound(SourceError):
    """Authoritative 404 — the resource does not exist (not retried)."""


@dataclass
class HttpResult:
    url: str
    status: int
    content: bytes
    content_type: str
    retrieved_at: datetime
    elapsed_ms: int
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        import json

        return json.loads(self.content)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8-sig", errors="replace")


class ResilientHttpClient:
    def __init__(
        self,
        user_agent: str,
        timeout_s: float = 30.0,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
        host_intervals: dict[str, float] | None = None,
        backoff_base_s: float = 1.0,
        max_backoff_s: float = 30.0,
        breaker_threshold: int = 5,
        breaker_reset_s: float = 120.0,
        rate_limits_enabled: bool = True,
    ) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=httpx.Timeout(timeout_s, connect=10.0),
            follow_redirects=True,
            transport=transport,
        )
        self.max_retries = max_retries
        self.host_intervals = {**DEFAULT_HOST_INTERVALS, **(host_intervals or {})} if rate_limits_enabled else {}
        self.backoff_base_s = backoff_base_s
        self.max_backoff_s = max_backoff_s
        self._limiter = MinIntervalLimiter()
        self._breakers: dict[str, CircuitBreaker] = {}
        self._breaker_cfg = (breaker_threshold, breaker_reset_s)

    def breaker(self, host: str) -> CircuitBreaker:
        if host not in self._breakers:
            self._breakers[host] = CircuitBreaker(*self._breaker_cfg)
        return self._breakers[host]

    def breaker_states(self) -> dict[str, str]:
        return {h: b.state for h, b in self._breakers.items()}

    def get(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> HttpResult:
        host = urlsplit(url).netloc
        breaker = self.breaker(host)
        if not breaker.allow():
            METRICS.inc("source_requests", host=host, outcome="circuit_open")
            raise SourceUnavailable(url, "circuit open")
        attempt = 0
        status: int | None
        retry_after: str | None
        err: str | None
        while True:
            attempt += 1
            self._limiter.wait(host, self.host_intervals.get(host, 0.0))
            started = time.perf_counter()
            try:
                resp = self._client.get(url, params=params, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                status, retry_after, err = None, None, f"{type(exc).__name__}: {exc}"
            else:
                status, retry_after, err = resp.status_code, resp.headers.get("retry-after"), None
                if status < 400:
                    elapsed = int((time.perf_counter() - started) * 1000)
                    breaker.record_success()
                    METRICS.inc("source_requests", host=host, outcome="ok")
                    METRICS.observe("source_latency_seconds", elapsed / 1000, host=host)
                    return HttpResult(
                        url=str(resp.url),
                        status=status,
                        content=resp.content,
                        content_type=resp.headers.get("content-type", ""),
                        retrieved_at=datetime.now(UTC),
                        elapsed_ms=elapsed,
                        headers=dict(resp.headers),
                    )
                if status == 404:
                    breaker.record_success()  # the host is healthy; the resource does not exist
                    METRICS.inc("source_requests", host=host, outcome="not_found")
                    raise SourceNotFound(url, "not found", 404)
                if status not in RETRYABLE:
                    breaker.record_failure()
                    METRICS.inc("source_requests", host=host, outcome="client_error")
                    raise SourceUnavailable(url, "non-retryable HTTP error", status)
            if attempt > self.max_retries:
                breaker.record_failure()
                METRICS.inc("source_requests", host=host, outcome="exhausted")
                log_event(log, "source_request_failed", url=url, status=status, error=err, attempts=attempt)
                raise SourceUnavailable(url, err or "retries exhausted", status)
            delay = min(self.max_backoff_s, self.backoff_base_s * 2 ** (attempt - 1)) * (0.5 + random.random() / 2)
            if retry_after and retry_after.isdigit():
                delay = min(self.max_backoff_s, max(delay, float(retry_after)))
            METRICS.inc("source_retries", host=host)
            time.sleep(delay)

    def close(self) -> None:
        self._client.close()
