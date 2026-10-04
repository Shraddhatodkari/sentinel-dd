"""AWS Bedrock provider (Converse API) — hyperscaler-hosted Claude / Llama / Titan."""

from __future__ import annotations

from typing import Any

from sentinel.llm.base import LLMClient, LLMError, LLMResponse, Message, Usage


class BedrockClient(LLMClient):
    provider = "bedrock"

    def __init__(self, model_id: str, region: str, *, client: Any | None = None):
        self.model = model_id
        if client is None:
            import boto3

            client = boto3.client("bedrock-runtime", region_name=region)
        self._client = client

    def complete(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse:
        req: dict[str, Any] = {
            "modelId": self.model,
            "messages": [{"role": m.role, "content": [{"text": m.content}]} for m in messages],
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
        }
        if system:
            req["system"] = [{"text": system}]
        try:
            resp = self._client.converse(**req)
        except Exception as exc:
            raise LLMError(f"Bedrock call failed: {exc}") from exc
        parts = resp["output"]["message"]["content"]
        text = "".join(p.get("text", "") for p in parts)
        u = resp.get("usage", {})
        return LLMResponse(
            text,
            self.provider,
            self.model,
            Usage(u.get("inputTokens", 0), u.get("outputTokens", 0)),
            resp.get("stopReason"),
        )
