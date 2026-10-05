"""Anthropic Claude provider (Messages API) with prompt caching and retry/backoff."""

from __future__ import annotations

import time
from typing import Any

from sentinel.llm.base import LLMClient, LLMError, LLMResponse, Message, Usage


class AnthropicClient(LLMClient):
    provider = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout_s: float = 60.0,
        max_retries: int = 3,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.max_retries = max_retries
        if client is None:
            import anthropic  # local import keeps the SDK optional for non-Claude deployments

            client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=0)
        self._client = client

    def complete(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if system:
            # Mark the (large, stable) system prompt as cacheable to cut cost/latency.
            kwargs["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

        delay = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                # latency, tokens, cost and errors are metered once, in MeteredLLM / LLMRouter
                resp = self._client.messages.create(**kwargs)
                text = "".join(
                    getattr(block, "text", "") for block in resp.content if getattr(block, "type", "") == "text"
                )
                usage = Usage(
                    input_tokens=getattr(resp.usage, "input_tokens", 0),
                    output_tokens=getattr(resp.usage, "output_tokens", 0),
                )
                return LLMResponse(text, self.provider, self.model, usage, getattr(resp, "stop_reason", None))
            except Exception as exc:  # SDK raises typed errors; retry transient ones
                status = getattr(exc, "status_code", None)
                retryable = status in (408, 409, 429, 500, 502, 503, 529) or status is None
                if not retryable or attempt == self.max_retries:
                    raise LLMError(f"Anthropic call failed: {exc}") from exc
                time.sleep(delay)
                delay = min(delay * 2, 20)
        raise LLMError("unreachable")
