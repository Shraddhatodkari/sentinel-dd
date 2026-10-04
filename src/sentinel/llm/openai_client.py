"""OpenAI (and Azure OpenAI-compatible) provider."""

from __future__ import annotations

from typing import Any

from sentinel.llm.base import LLMClient, LLMError, LLMResponse, Message, Usage


class OpenAIClient(LLMClient):
    provider = "openai"

    def __init__(self, api_key: str, model: str, *, timeout_s: float = 60.0, client: Any | None = None):
        self.model = model
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, timeout=timeout_s, max_retries=3)
        self._client = client

    def complete(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse:
        msgs: list[Any] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs += [{"role": m.role, "content": m.content} for m in messages]
        try:
            resp = self._client.chat.completions.create(
                model=self.model, messages=msgs, max_tokens=max_tokens, temperature=temperature
            )
        except Exception as exc:
            raise LLMError(f"OpenAI call failed: {exc}") from exc
        choice = resp.choices[0]
        usage = Usage(getattr(resp.usage, "prompt_tokens", 0) or 0, getattr(resp.usage, "completion_tokens", 0) or 0)
        return LLMResponse(choice.message.content or "", self.provider, self.model, usage, choice.finish_reason)
