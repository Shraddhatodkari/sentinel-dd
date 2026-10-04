"""Multi-provider router: primary + ordered fallbacks with a per-provider circuit breaker.

Gives the platform hyperscaler portability (Anthropic direct, Bedrock, Vertex, OpenAI) and
resilience to single-vendor outages.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sentinel.config import Settings
from sentinel.llm.base import LLMClient, LLMError, LLMResponse, Message
from sentinel.observability.logging import get_logger, log_event
from sentinel.observability.metrics import METRICS

log = get_logger(__name__)


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    reset_after_s: float = 30.0
    failures: int = 0
    opened_at: float | None = None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if time.monotonic() - self.opened_at >= self.reset_after_s:
            self.opened_at = None  # half-open: allow a trial request
            self.failures = 0
            return True
        return False

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = time.monotonic()


class LLMRouter(LLMClient):
    provider = "router"

    def __init__(self, clients: list[LLMClient], breaker_threshold: int = 3, reset_after_s: float = 30.0):
        if not clients:
            raise ValueError("LLMRouter needs at least one client")
        self.clients = clients
        self.model = clients[0].model
        self.breakers = {id(c): CircuitBreaker(breaker_threshold, reset_after_s) for c in clients}

    def complete(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse:
        errors: list[str] = []
        for client in self.clients:
            breaker = self.breakers[id(client)]
            if not breaker.allow():
                errors.append(f"{client.provider}: circuit open")
                continue
            try:
                resp = client.complete(messages, system=system, max_tokens=max_tokens, temperature=temperature)
                breaker.record_success()
                return resp
            except LLMError as exc:
                breaker.record_failure()
                METRICS.inc("llm_fallbacks", provider=client.provider)
                log_event(log, "llm_provider_failed", provider=client.provider, error=str(exc))
                errors.append(f"{client.provider}: {exc}")
        raise LLMError("All LLM providers failed: " + "; ".join(errors))


def build_client(provider: str, settings: Settings) -> LLMClient:
    if provider == "fake":
        from sentinel.llm.fake import FakeLLM

        return FakeLLM()
    if provider == "anthropic":
        from sentinel.llm.anthropic_client import AnthropicClient

        if not settings.anthropic_api_key:
            raise LLMError("SENTINEL_ANTHROPIC_API_KEY not set")
        return AnthropicClient(
            settings.anthropic_api_key.get_secret_value(),
            settings.anthropic_model,
            timeout_s=settings.llm_timeout_s,
            max_retries=settings.llm_max_retries,
        )
    if provider == "openai":
        from sentinel.llm.openai_client import OpenAIClient

        if not settings.openai_api_key:
            raise LLMError("SENTINEL_OPENAI_API_KEY not set")
        return OpenAIClient(settings.openai_api_key.get_secret_value(), settings.openai_model)
    if provider == "bedrock":
        from sentinel.llm.bedrock_client import BedrockClient

        return BedrockClient(settings.bedrock_model_id, settings.aws_region)
    if provider == "vertex":
        from sentinel.llm.vertex_client import VertexClient

        if not settings.vertex_project:
            raise LLMError("SENTINEL_VERTEX_PROJECT not set")
        return VertexClient(settings.vertex_project, settings.vertex_location, settings.vertex_model)
    raise ValueError(f"Unknown LLM provider: {provider}")


def build_llm(settings: Settings) -> LLMClient:
    providers = [settings.llm_provider, *[p for p in settings.llm_fallbacks if p != settings.llm_provider]]
    clients = [build_client(p, settings) for p in providers]
    return clients[0] if len(clients) == 1 else LLMRouter(clients)
