"""Incremental Okapi BM25 sparse index (exact-term recall for names, IDs, legal terms)."""

from __future__ import annotations

import math
import re
import threading
from collections import Counter, defaultdict

from sentinel.domain import Chunk, ScoredChunk
from sentinel.vectorstore.base import MetadataFilter, matches

_TOKEN = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "was",
        "were",
        "will",
        "with",
        "this",
        "what",
        "which",
        "who",
        "whom",
        "does",
        "did",
        "do",
        "how",
    ]
)


def light_stem(t: str) -> str:
    """Tiny, dependency-free suffix stripper (certifications->certification, owns->own)."""
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 3 and t.endswith("s") and not t.endswith(("ss", "us", "is")):
        return t[:-1]
    return t


def tokenize(text: str) -> list[str]:
    return [light_stem(t) for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS]


class BM25Index:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self._docs: dict[str, Chunk] = {}
        self._tf: dict[str, Counter[str]] = {}
        self._df: defaultdict[str, int] = defaultdict(int)
        self._len: dict[str, int] = {}
        self._lock = threading.RLock()

    def add(self, chunks: list[Chunk]) -> None:
        with self._lock:
            for ch in chunks:
                if ch.id in self._docs:
                    self._remove(ch.id)
                toks = tokenize(ch.text)
                tf = Counter(toks)
                self._docs[ch.id] = ch
                self._tf[ch.id] = tf
                self._len[ch.id] = len(toks)
                for term in tf:
                    self._df[term] += 1

    def _remove(self, cid: str) -> None:
        for term in self._tf[cid]:
            self._df[term] -= 1
            if self._df[term] == 0:
                del self._df[term]
        del self._docs[cid], self._tf[cid], self._len[cid]

    def delete_document(self, doc_id: str) -> int:
        with self._lock:
            ids = [cid for cid, c in self._docs.items() if c.doc_id == doc_id]
            for cid in ids:
                self._remove(cid)
            return len(ids)

    def search(self, query: str, k: int = 5, flt: MetadataFilter | None = None) -> list[ScoredChunk]:
        with self._lock:
            n = len(self._docs)
            if n == 0:
                return []
            avgdl = sum(self._len.values()) / n
            q = set(tokenize(query))
            scores: dict[str, float] = defaultdict(float)
            for term in q:
                df = self._df.get(term)
                if not df:
                    continue
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                for cid, tf in self._tf.items():
                    f = tf.get(term)
                    if f:
                        denom = f + self.k1 * (1 - self.b + self.b * self._len[cid] / avgdl)
                        scores[cid] += idf * f * (self.k1 + 1) / denom
            ranked = sorted(scores.items(), key=lambda kv: -kv[1])
            out = []
            for cid, s in ranked:
                if matches(self._docs[cid].metadata, flt):
                    out.append(ScoredChunk(self._docs[cid], s, "sparse"))
                    if len(out) >= k:
                        break
            return out

    def __len__(self) -> int:
        return len(self._docs)
