"""Provider-agnostic LLM contract.

Every provider (Anthropic Claude, OpenAI, AWS Bedrock, GCP Vertex AI, and a deterministic
fake for tests) implements :class:`LLMClient`. Higher layers never import a vendor SDK.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ValidationError

Role = Literal["user", "assistant"]
T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    usage: Usage = field(default_factory=Usage)
    stop_reason: str | None = None


class LLMError(RuntimeError):
    """Raised for unrecoverable provider errors."""


class StructuredOutputError(LLMError):
    """Raised when a model cannot produce output matching the requested schema."""


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Extract the first JSON object/array from model output (tolerates code fences / prose)."""
    m = _JSON_BLOCK.search(text)
    candidate = m.group(1) if m else text
    candidate = candidate.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    # fall back: scan for the first balanced {...} or [...]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = candidate.find(opener)
        while start != -1:
            depth = 0
            for i in range(start, len(candidate)):
                if candidate[i] == opener:
                    depth += 1
                elif candidate[i] == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(candidate[start : i + 1])
                        except json.JSONDecodeError:
                            break
            start = candidate.find(opener, start + 1)
    raise StructuredOutputError(f"No valid JSON found in model output: {text[:200]!r}")


class LLMClient(ABC):
    provider: str = "base"
    model: str = "unknown"

    @abstractmethod
    def complete(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse: ...

    def complete_structured(
        self,
        messages: list[Message],
        schema: type[T],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        repair_attempts: int = 2,
    ) -> T:
        """Return a validated pydantic object. Self-repairs by feeding validation errors back."""
        schema_json = json.dumps(schema.model_json_schema())
        sys_prompt = (system or "") + (
            "\n\nRespond ONLY with a JSON object that validates against this JSON Schema:\n" + schema_json
        )
        convo = list(messages)
        last_err: Exception | None = None
        for _ in range(repair_attempts + 1):
            resp = self.complete(convo, system=sys_prompt, max_tokens=max_tokens, temperature=0.0)
            try:
                return schema.model_validate(extract_json(resp.text))
            except (StructuredOutputError, ValidationError) as exc:
                last_err = exc
                convo = [
                    *convo,
                    Message("assistant", resp.text),
                    Message("user", f"Your output was invalid: {exc}. Return corrected JSON only."),
                ]
        raise StructuredOutputError(f"Failed to obtain valid {schema.__name__}: {last_err}")
