"""Production hybrid retriever.

Pipeline: query analysis (structured filter extraction) -> dense + sparse candidate generation
-> Reciprocal Rank Fusion -> reranking -> MMR diversification -> context sanitisation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from sentinel.domain import ScoredChunk
from sentinel.embeddings.base import Embedder
from sentinel.rag.bm25 import BM25Index, tokenize
from sentinel.vectorstore.base import MetadataFilter, VectorStore

DOC_TYPE_HINTS = {
    "media": ("news", "media", "press", "article", "adverse", "enforcement", "charged", "lawsuit", "investigation"),
    "registry": (
        "registry",
        "incorporat",
        "registered",
        "shareholder",
        "ownership",
        "director",
        "officer",
        "parent",
        "subsidiar",
        "lei",
    ),
    "financials": ("revenue", "financial", "profit", "debt", "income", "assets", "liabilities", "equity"),
    "filing": ("risk factor", "legal proceeding", "cybersecurity", "disclose", "10-k", "annual report", "business"),
    "screening": ("sanction", "pep", "politically exposed", "watch list", "watchlist", "designat"),
}
KINDS_FOR_HINT = {
    "media": ("media_article", "enforcement_release"),
    "registry": ("registry_profile", "lei_record", "ownership", "subsidiaries", "insider"),
    "financials": ("financial_facts",),
    "filing": ("filing_text",),
    "screening": ("sanctions_record", "pep_record"),
}


@dataclass
class QueryPlan:
    text: str
    filters: MetadataFilter
    doc_types: list[str]


def analyze_query(query: str, known_entities: list[str] | None = None) -> QueryPlan:
    """Structured-retrieval pattern: derive metadata filters / boosts from the natural-language query.

    Entity filters are *hard* (precision); evidence-kind hints are *soft* (used for reranking boosts).
    """
    q = query.lower()
    filters: MetadataFilter = {}
    for ent in known_entities or []:
        if ent.lower() in q:
            filters["entity"] = ent
            break
    hints = [t for t, words in DOC_TYPE_HINTS.items() if any(w in q for w in words)]
    kinds = sorted({k for h in hints for k in KINDS_FOR_HINT[h]})
    return QueryPlan(query, filters, kinds)


def rrf(rankings: list[list[ScoredChunk]], k: int = 60, weights: list[float] | None = None) -> list[ScoredChunk]:
    """Reciprocal Rank Fusion — robust, score-scale-free fusion of heterogeneous rankers."""
    weights = weights or [1.0] * len(rankings)
    fused: dict[str, float] = {}
    by_id: dict[str, ScoredChunk] = {}
    for w, ranking in zip(weights, rankings, strict=True):
        for rank, sc in enumerate(ranking):
            fused[sc.chunk.id] = fused.get(sc.chunk.id, 0.0) + w / (k + rank + 1)
            by_id.setdefault(sc.chunk.id, sc)
    return [ScoredChunk(by_id[cid].chunk, s, "hybrid") for cid, s in sorted(fused.items(), key=lambda kv: -kv[1])]


class LexicalReranker:
    """Cheap cross-attention proxy: query-term coverage + phrase proximity. Swap for a
    cross-encoder (e.g. ``bge-reranker``) or Cohere/Voyage rerank via the same ``rerank`` API."""

    def rerank(self, query: str, cands: list[ScoredChunk], doc_types: list[str] | None = None) -> list[ScoredChunk]:
        q = set(tokenize(query))
        out = []
        for sc in cands:
            toks = tokenize(sc.chunk.text)
            coverage = len(q & set(toks)) / max(len(q), 1)
            boost = (
                0.15
                if doc_types and sc.chunk.metadata.get("kind", sc.chunk.metadata.get("doc_type")) in doc_types
                else 0.0
            )
            out.append(ScoredChunk(sc.chunk, 0.6 * coverage + 0.4 * min(sc.score * 30, 1.0) + boost, sc.source))
        return sorted(out, key=lambda s: -s.score)


def mmr(query_vec: np.ndarray, cands: list[ScoredChunk], vecs: np.ndarray, k: int, lam: float = 0.7) -> list[int]:
    """Maximal Marginal Relevance: balance relevance vs. redundancy."""
    if not cands:
        return []
    rel = vecs @ query_vec
    chosen: list[int] = []
    remaining = list(range(len(cands)))
    while remaining and len(chosen) < k:

        def score(i: int) -> float:
            red = max((float(vecs[i] @ vecs[j]) for j in chosen), default=0.0)
            return lam * float(rel[i]) - (1 - lam) * red

        best = max(remaining, key=score)
        chosen.append(best)
        remaining.remove(best)
    return chosen


class HybridRetriever:
    def __init__(
        self,
        embedder: Embedder,
        store: VectorStore,
        bm25: BM25Index,
        reranker: LexicalReranker | None = None,
        candidate_k: int = 30,
    ) -> None:
        self.embedder = embedder
        self.store = store
        self.bm25 = bm25
        self.reranker = reranker or LexicalReranker()
        self.candidate_k = candidate_k
        self.known_entities: list[str] = []

    def retrieve(
        self, query: str, k: int = 6, flt: MetadataFilter | None = None, use_mmr: bool = True
    ) -> list[ScoredChunk]:
        plan = analyze_query(query, self.known_entities)
        merged_filter = {**plan.filters, **(flt or {})}
        qv = self.embedder.embed_one(query)
        dense = self.store.search(qv, self.candidate_k, merged_filter)
        sparse = self.bm25.search(query, self.candidate_k, merged_filter)
        if not dense and not sparse and plan.filters and not flt:
            # filters too strict -> graceful degradation to unfiltered search
            dense = self.store.search(qv, self.candidate_k)
            sparse = self.bm25.search(query, self.candidate_k)
        fused = rrf([dense, sparse])[: self.candidate_k]
        ranked = self.reranker.rerank(query, fused, plan.doc_types)
        if not use_mmr or len(ranked) <= k:
            return ranked[:k]
        pool = ranked[: max(k * 3, 10)]
        vecs = self.embedder.embed([sc.chunk.text for sc in pool])
        return [pool[i] for i in mmr(qv, pool, vecs, k)]
