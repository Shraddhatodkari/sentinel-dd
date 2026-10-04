"""Lightweight in-process metrics registry with Prometheus text exposition.

Uses ``prometheus_client`` semantics without requiring it, so the service exposes
``/metrics`` in every environment. Thread-safe.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._hist_sum: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._hist_count: dict[tuple[str, tuple[tuple[str, str], ...]], int] = defaultdict(int)

    @staticmethod
    def _key(name: str, labels: dict[str, str] | None) -> tuple[str, tuple[tuple[str, str], ...]]:
        return name, tuple(sorted((labels or {}).items()))

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        with self._lock:
            self._counters[self._key(name, labels)] += value

    def observe(self, name: str, value: float, **labels: str) -> None:
        k = self._key(name, labels)
        with self._lock:
            self._hist_sum[k] += value
            self._hist_count[k] += 1

    @contextmanager
    def timer(self, name: str, **labels: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.observe(name, time.perf_counter() - start, **labels)

    def get(self, name: str, **labels: str) -> float:
        return self._counters.get(self._key(name, labels), 0.0)

    def render_prometheus(self) -> str:
        def fmt(labels: tuple[tuple[str, str], ...]) -> str:
            if not labels:
                return ""
            return "{" + ",".join(f'{k}="{v}"' for k, v in labels) + "}"

        lines: list[str] = []
        with self._lock:
            for (n, lbl), v in sorted(self._counters.items()):
                lines.append(f"{n}_total{fmt(lbl)} {v}")
            for (n, lbl), s in sorted(self._hist_sum.items()):
                lines.append(f"{n}_sum{fmt(lbl)} {s:.6f}")
                lines.append(f"{n}_count{fmt(lbl)} {self._hist_count[(n, lbl)]}")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._hist_sum.clear()
            self._hist_count.clear()


METRICS = Metrics()
