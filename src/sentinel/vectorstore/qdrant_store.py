"""Qdrant-backed vector store (HNSW, payload filtering, horizontal scale)."""

from __future__ import annotations

import uuid
from typing import Any

import numpy as np

from sentinel.domain import Chunk, ScoredChunk
from sentinel.vectorstore.base import MetadataFilter, VectorStore


class QdrantVectorStore(VectorStore):
    def __init__(self, url: str, collection: str, dim: int, client: Any | None = None) -> None:
        from qdrant_client import QdrantClient, models

        self._m = models
        self._client = client or QdrantClient(url=url)
        self.collection = collection
        self.dim = dim
        if not self._client.collection_exists(collection):
            self._client.create_collection(
                collection, vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE)
            )

    @staticmethod
    def _pid(chunk_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))

    def upsert(self, chunks: list[Chunk], vectors: np.ndarray) -> None:
        points = [
            self._m.PointStruct(
                id=self._pid(c.id),
                vector=v.tolist(),
                payload={"chunk_id": c.id, "doc_id": c.doc_id, "text": c.text, **c.metadata},
            )
            for c, v in zip(chunks, vectors, strict=True)
        ]
        self._client.upsert(self.collection, points=points)

    def _filter(self, flt: MetadataFilter | None) -> Any:
        if not flt:
            return None
        must = []
        for k, cond in flt.items():
            if isinstance(cond, dict):
                must.append(
                    self._m.FieldCondition(key=k, range=self._m.Range(gte=cond.get("$gte"), lte=cond.get("$lte")))
                )
            elif isinstance(cond, list | tuple | set):
                must.append(self._m.FieldCondition(key=k, match=self._m.MatchAny(any=list(cond))))
            else:
                must.append(self._m.FieldCondition(key=k, match=self._m.MatchValue(value=cond)))
        return self._m.Filter(must=must)

    def search(self, vector: np.ndarray, k: int = 5, flt: MetadataFilter | None = None) -> list[ScoredChunk]:
        res = self._client.query_points(
            self.collection, query=vector.tolist(), limit=k, query_filter=self._filter(flt), with_payload=True
        ).points
        out = []
        for p in res:
            pl = dict(p.payload)
            meta = {k: v for k, v in pl.items() if k not in ("chunk_id", "doc_id", "text")}
            out.append(ScoredChunk(Chunk(pl["chunk_id"], pl["doc_id"], pl["text"], meta), float(p.score), "dense"))
        return out

    def delete_document(self, doc_id: str) -> int:
        flt = self._m.Filter(must=[self._m.FieldCondition(key="doc_id", match=self._m.MatchValue(value=doc_id))])
        before = self.count()
        self._client.delete(self.collection, points_selector=self._m.FilterSelector(filter=flt))
        return before - self.count()

    def count(self) -> int:
        return int(self._client.count(self.collection).count)

    def all_chunks(self) -> list[Chunk]:
        out, offset = [], None
        while True:
            pts, offset = self._client.scroll(self.collection, limit=512, offset=offset, with_payload=True)
            for p in pts:
                pl = dict(p.payload)
                meta = {k: v for k, v in pl.items() if k not in ("chunk_id", "doc_id", "text")}
                out.append(Chunk(pl["chunk_id"], pl["doc_id"], pl["text"], meta))
            if offset is None:
                return out
