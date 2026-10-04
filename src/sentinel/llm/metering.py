"""LLM usage metering: every completion is recorded (provider, model, tokens, latency, estimated cost) against
the current case/purpose — persisted in ``llm_usage`` and exported as metrics."""

from __future__ import annotations

import contextvars
import time
from collections.abc import Iterator
from contextlib import contextmanager

from sentinel.db.base import Database
from sentinel.db.models import LLMUsage
from sentinel.llm.base import LLMClient, LLMResponse, Message
from sentinel.observability.metrics import METRICS

_ctx: contextvars.ContextVar[tuple[str | None, str]] = contextvars.ContextVar("llm_ctx", default=(None, "unspecified"))


@contextmanager
def llm_context(case_id: str | None, purpose: str) -> Iterator[None]:
    token = _ctx.set((case_id, purpose))
    try:
        yield
    finally:
        _ctx.reset(token)


class MeteredLLM(LLMClient):
    def __init__(self, inner: LLMClient, db: Database | None, prices: dict[str, tuple[float, float]]) -> None:
        self.inner = inner
        self.db = db
        self.prices = prices
        self.provider = inner.provider
        self.model = inner.model

    def complete(
        self, messages: list[Message], *, system: str | None = None, max_tokens: int = 1024, temperature: float = 0.0
    ) -> LLMResponse:
        started = time.perf_counter()
        resp = self.inner.complete(messages, system=system, max_tokens=max_tokens, temperature=temperature)
        latency = int((time.perf_counter() - started) * 1000)
        pin, pout = self.prices.get(resp.model, (0.0, 0.0))
        cost = (resp.usage.input_tokens * pin + resp.usage.output_tokens * pout) / 1_000_000
        case_id, purpose = _ctx.get()
        METRICS.inc("llm_requests", provider=resp.provider, purpose=purpose)
        METRICS.inc("llm_tokens", resp.usage.total, provider=resp.provider)
        METRICS.inc("llm_cost_usd", cost, provider=resp.provider)
        METRICS.observe("llm_latency_seconds", latency / 1000, provider=resp.provider)
        if self.db is not None:
            with self.db.session() as s:
                s.add(
                    LLMUsage(
                        case_id=case_id,
                        purpose=purpose,
                        provider=resp.provider,
                        model=resp.model,
                        input_tokens=resp.usage.input_tokens,
                        output_tokens=resp.usage.output_tokens,
                        cost_usd=round(cost, 6),
                        latency_ms=latency,
                    )
                )
        return resp
