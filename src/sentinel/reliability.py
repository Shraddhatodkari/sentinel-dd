"""Shared reliability primitives: circuit breaker and per-key rate limiting (thread-safe)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class CircuitBreaker:
    """Classic closed → open → half-open breaker."""

    failure_threshold: int = 5
    reset_after_s: float = 60.0
    failures: int = 0
    opened_at: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half-open" if time.monotonic() - self.opened_at >= self.reset_after_s else "open"

    def allow(self) -> bool:
        with self._lock:
            if self.opened_at is None:
                return True
            return time.monotonic() - self.opened_at >= self.reset_after_s  # True → half-open trial

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.failures >= self.failure_threshold:
                self.opened_at = time.monotonic()


class MinIntervalLimiter:
    """Enforces a minimum interval between calls per key (e.g. SEC: 10 req/s, GDELT: 1 req/5 s)."""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, key: str, min_interval_s: float) -> float:
        if min_interval_s <= 0:
            return 0.0
        with self._lock:
            now = time.monotonic()
            next_ok = self._last.get(key, 0.0) + min_interval_s
            delay = max(0.0, next_ok - now)
            self._last[key] = max(now, next_ok)
        if delay:
            time.sleep(delay)
        return delay
