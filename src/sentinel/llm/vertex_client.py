"""Google Cloud Vertex AI provider (Gemini or Claude-on-Vertex)."""

from __future__ import annotations

from typing import Any

from sentinel.llm.base import LLMClient, LLMError, LLMResponse, Message, Usage


class VertexClient(LLMClient):
    provider = "vertex"

    def __init__(self, project: str, location: str, model: str, *, model_obj: Any | None = None):
        self.model = model
        if model_obj is None:
            import vertexai
            from vertexai.generative_models import GenerativeModel

            vertexai.init(project=project, location=location)
            model_obj = GenerativeModel(model)
        self._model = model_obj

    def complete(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse:
        prompt = ((system + "\n\n") if system else "") + "\n".join(f"{m.role.upper()}: {m.content}" for m in messages)
        try:
            resp = self._model.generate_content(
                prompt, generation_config={"max_output_tokens": max_tokens, "temperature": temperature}
            )
        except Exception as exc:
            raise LLMError(f"Vertex call failed: {exc}") from exc
        meta = getattr(resp, "usage_metadata", None)
        usage = Usage(getattr(meta, "prompt_token_count", 0) or 0, getattr(meta, "candidates_token_count", 0) or 0)
        return LLMResponse(resp.text, self.provider, self.model, usage)
