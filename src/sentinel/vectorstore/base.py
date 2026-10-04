"""Vector store contract + exact in-memory implementation with metadata filtering."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from sentinel.domain import Chunk, ScoredChunk

MetadataFilter = dict[str, Any]


def matches(meta: dict[str, Any], flt: MetadataFilter | None) -> bool:
    """Filter semantics: scalar -> equality, list -> membership, {"$gte"/"$lte": x} -> range."""
    if not flt:
        return True
    for key, cond in flt.items():
        val = meta.get(key)
        if isinstance(cond, dict):
            if "$gte" in cond and (val is None or val < cond["$gte"]):
                return False
            if "$lte" in cond and (val is None or val > cond["$lte"]):
                return False
            if "$ne" in cond and val == cond["$ne"]:
                return False
        elif isinstance(cond, list | tuple | set):
            if val not in cond:
                return False
        elif val != cond:
            return False
    return True


class VectorStore(ABC):
    @abstractmethod
    def upsert(self, chunks: list[Chunk], vectors: np.ndarray) -> None: ...

    @abstractmethod
    def search(self, vector: np.ndarray, k: int = 5, flt: MetadataFilter | None = None) -> list[ScoredChunk]: ...

    @abstractmethod
    def delete_document(self, doc_id: str) -> int: ...

    @abstractmethod
    def count(self) -> int: ...

    @abstractmethod
    def all_chunks(self) -> list[Chunk]: ...


class InMemoryVectorStore(VectorStore):
    """Exact cosine search over a contiguous matrix. Suitable to ~1e5 chunks; swap for
    Qdrant/pgvector (same interface) beyond that."""

    def __init__(self, dim: int) -> None:
        self.dim = dim
        self._ids: list[str] = []
        self._chunks: dict[str, Chunk] = {}
        self._matrix = np.zeros((0, dim), dtype=np.float32)
        self._lock = threading.RLock()

    def upsert(self, chunks: list[Chunk], vectors: np.ndarray) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors length mismatch")
        if len(chunks) and vectors.shape[1] != self.dim:
            raise ValueError(f"expected dim {self.dim}, got {vectors.shape[1]}")
        with self._lock:
            existing = {cid: i for i, cid in enumerate(self._ids)}
            new_rows: list[np.ndarray] = []
            for ch, vec in zip(chunks, vectors, strict=True):
                self._chunks[ch.id] = ch
                if ch.id in existing:
                    self._matrix[existing[ch.id]] = vec
                else:
                    self._ids.append(ch.id)
                    new_rows.append(vec)
            if new_rows:
                self._matrix = np.vstack([self._matrix, np.asarray(new_rows, dtype=np.float32)])

    def search(self, vector: np.ndarray, k: int = 5, flt: MetadataFilter | None = None) -> list[ScoredChunk]:
        with self._lock:
            if not self._ids:
                return []
            scores = self._matrix @ vector.astype(np.float32)
            order = np.argsort(-scores)
            out: list[ScoredChunk] = []
            for idx in order:
                ch = self._chunks[self._ids[idx]]
                if matches(ch.metadata, flt):
                    out.append(ScoredChunk(ch, float(scores[idx]), "dense"))
                    if len(out) >= k:
                        break
            return out

    def delete_document(self, doc_id: str) -> int:
        with self._lock:
            keep = [i for i, cid in enumerate(self._ids) if self._chunks[cid].doc_id != doc_id]
            removed = len(self._ids) - len(keep)
            for cid in [c for c in self._ids if self._chunks[c].doc_id == doc_id]:
                del self._chunks[cid]
            self._ids = [self._ids[i] for i in keep]
            self._matrix = self._matrix[keep] if keep else np.zeros((0, self.dim), dtype=np.float32)
            return removed

    def count(self) -> int:
        return len(self._ids)

    def all_chunks(self) -> list[Chunk]:
        with self._lock:
            return [self._chunks[c] for c in self._ids]
