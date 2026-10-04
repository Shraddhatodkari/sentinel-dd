"""Grounded question answering = retrieve -> sanitise -> generate -> verify."""

from __future__ import annotations

from sentinel.domain import Evidence
from sentinel.llm.base import LLMClient, Message
from sentinel.observability.metrics import METRICS
from sentinel.prompts import GROUNDED_QA_SYSTEM, GROUNDED_QA_USER
from sentinel.rag.grounding import GroundingChecker
from sentinel.rag.retriever import HybridRetriever
from sentinel.security.guardrails import Guardrails
from sentinel.vectorstore.base import MetadataFilter


class GroundedQA:
    def __init__(
        self,
        retriever: HybridRetriever,
        llm: LLMClient,
        guardrails: Guardrails,
        grounding: GroundingChecker | None = None,
        max_context_chars: int = 6000,
    ) -> None:
        self.retriever = retriever
        self.llm = llm
        self.guardrails = guardrails
        self.grounding = grounding or GroundingChecker()
        self.max_context_chars = max_context_chars

    def answer(self, question: str, k: int = 6, flt: MetadataFilter | None = None) -> Evidence:
        hits = self.retriever.retrieve(question, k=k, flt=flt)
        flagged = False
        lines: list[str] = []
        budget = self.max_context_chars
        for sc in hits:
            text, was_flagged = self.guardrails.sanitize_context(sc.chunk.text)
            flagged |= was_flagged
            line = f"[{sc.chunk.id}] {text}"
            if len(line) > budget:
                break
            budget -= len(line)
            lines.append(line)
        if was_any := bool(lines):
            prompt = GROUNDED_QA_USER.format(question=question, context="\n".join(lines))
            resp = self.llm.complete([Message("user", prompt)], system=GROUNDED_QA_SYSTEM, max_tokens=600)
            answer = resp.text.strip()
        else:
            answer = "INSUFFICIENT_EVIDENCE: no relevant documents were found."
        report = self.grounding.check(answer, [h.chunk for h in hits])
        METRICS.inc("rag_queries")
        METRICS.observe("rag_grounding_score", report.score)
        if flagged:
            METRICS.inc("indirect_injection_blocked")
        return Evidence(
            question=question,
            answer=answer,
            citations=report.citations,
            grounding_score=report.score if was_any else 0.0,
            chunks=hits,
            flagged_injection=flagged,
        )
