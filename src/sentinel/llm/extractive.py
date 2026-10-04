"""Extractive (no-generation) language backend for offline / air-gapped operation and deterministic testing.

It never composes new facts: answers are verbatim sentences selected from the supplied CONTEXT, each with its
citation tag. Use it when no LLM provider is approved, or as the last-resort fallback in ``LLMRouter``.

Behaviour:
* If the system prompt asks for JSON (structured output), it synthesises a schema-valid instance
  (or uses a scripted responder registered for that schema title).
* Otherwise it acts as an *extractive* summariser: it selects the sentences from the ``CONTEXT``
  block that best overlap the ``QUESTION`` and returns them with their ``[source]`` citation tags.
  This makes grounding/citation logic testable end-to-end without network access.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from sentinel.llm.base import LLMClient, LLMResponse, Message, Usage

_WORD = re.compile(r"[a-z0-9]+")
_CTX_LINE = re.compile(r"^\[(?P<src>[^\]]+)\]\s*(?P<text>.+)$")
_STOP = {"the", "a", "an", "of", "and", "or", "to", "in", "is", "are", "for", "on", "with", "what", "which"}


def _tokens(s: str) -> set[str]:
    from sentinel.rag.bm25 import light_stem

    return {light_stem(t) for t in _WORD.findall(s.lower()) if t not in _STOP and len(t) > 2}


def _instance_from_schema(schema: dict[str, Any], defs: dict[str, Any]) -> Any:
    if "$ref" in schema:
        return _instance_from_schema(defs[schema["$ref"].split("/")[-1]], defs)
    if "default" in schema:
        return schema["default"]
    if "anyOf" in schema:
        return _instance_from_schema(schema["anyOf"][0], defs)
    if "enum" in schema:
        return schema["enum"][0]
    t = schema.get("type")
    if t == "object":
        return {
            k: _instance_from_schema(v, defs)
            for k, v in schema.get("properties", {}).items()
            if k in schema.get("required", [])
        }
    if t == "array":
        return []
    if t == "string":
        return ""
    if t in ("number", "integer"):
        return schema.get("minimum", 0)
    if t == "boolean":
        return False
    return None


class ExtractiveLLM(LLMClient):
    provider = "extractive"
    model = "extractive-v1"

    def __init__(self, scripted: dict[str, Callable[[list[Message]], Any]] | None = None, max_sentences: int = 3):
        self.scripted = scripted or {}
        self.max_sentences = max_sentences
        self.calls: list[tuple[str | None, list[Message]]] = []

    def complete(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse:
        self.calls.append((system, messages))
        prompt = "\n".join(m.content for m in messages)
        text = self._structured(system, messages) if system and "JSON Schema" in system else self._extractive(prompt)
        usage = Usage(input_tokens=len(prompt) // 4, output_tokens=len(text) // 4)
        return LLMResponse(text, self.provider, self.model, usage, "end_turn")

    def _structured(self, system: str, messages: list[Message]) -> str:
        schema = json.loads(system.split("JSON Schema:\n", 1)[1])
        title = schema.get("title", "")
        if title in self.scripted:
            return json.dumps(self.scripted[title](messages))
        return json.dumps(_instance_from_schema(schema, schema.get("$defs", {})))

    def _extractive(self, prompt: str) -> str:
        question = ""
        ctx: list[tuple[str, str]] = []
        in_ctx = False
        for line in prompt.splitlines():
            if line.startswith("QUESTION:"):
                question = line.removeprefix("QUESTION:").strip()
            if line.strip() == "CONTEXT:":
                in_ctx = True
                continue
            if in_ctx:
                m = _CTX_LINE.match(line.strip())
                if m:
                    ctx.append((m.group("src"), m.group("text")))
        if not ctx:
            return "INSUFFICIENT_EVIDENCE: no context was provided."
        q = _tokens(question)
        scored: list[tuple[float, int, str, str]] = []
        for idx, (src, text) in enumerate(ctx):
            head, sep, body = text.partition(": ")
            if sep and " > " in head:  # drop the "Title > Heading:" chunk prefix
                text = body
            for sent in re.split(r"(?<=[.!?])\s+", text):
                overlap = len(q & _tokens(sent))
                if overlap:
                    scored.append((overlap, -idx, src, sent.strip()))
        if not scored:
            return "INSUFFICIENT_EVIDENCE: the context does not address the question."
        scored.sort(reverse=True)
        picked = scored[: self.max_sentences]
        return " ".join(f"{s} [{src}]" for _, _, src, s in picked)
