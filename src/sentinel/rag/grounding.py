"""Grounding / faithfulness verification.

Every sentence of a generated answer is treated as a claim. A claim is *supported* when it
cites a retrieved chunk AND has sufficient lexical entailment with that chunk. The
grounding score is the fraction of supported claims; unsupported claims are surfaced so the
critic agent can trigger re-retrieval or the policy engine can force human review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sentinel.domain import Chunk
from sentinel.rag.bm25 import tokenize

_CIT = re.compile(r"\[([^\]\s]+#\d+)\]")
# split after a sentence terminator or a citation, but never between a sentence and its citation
_SPLIT = re.compile(r"(?<=[.!?\]])\s+(?=[A-Z])")


@dataclass
class GroundingReport:
    score: float
    supported: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    invalid_citations: list[str] = field(default_factory=list)

    @property
    def citations(self) -> list[str]:
        return sorted({c for s in self.supported for c in _CIT.findall(s)})


class GroundingChecker:
    def __init__(self, min_overlap: float = 0.6) -> None:
        self.min_overlap = min_overlap

    def check(self, answer: str, chunks: list[Chunk]) -> GroundingReport:
        if answer.strip().startswith("INSUFFICIENT_EVIDENCE"):
            return GroundingReport(1.0, supported=[answer])  # abstention is faithful
        by_id = {c.id: c for c in chunks}
        rep = GroundingReport(0.0)
        claims = [s.strip() for s in _SPLIT.split(answer) if len(tokenize(s)) >= 3]
        if not claims:
            return rep
        for claim in claims:
            cites = _CIT.findall(claim)
            bad = [c for c in cites if c not in by_id]
            rep.invalid_citations += bad
            good = [by_id[c] for c in cites if c in by_id]
            body = set(tokenize(_CIT.sub("", claim)))
            ok = any(len(body & set(tokenize(ch.text))) / max(len(body), 1) >= self.min_overlap for ch in good)
            (rep.supported if ok else rep.unsupported).append(claim)
        rep.score = len(rep.supported) / len(claims)
        return rep
