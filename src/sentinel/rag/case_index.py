"""Per-case retrieval index.

Chunks (text + embedding) are persisted in the ``chunks`` table and always scoped to one case: retrieval for a
case can only ever see that case's evidence (no cross-case leakage). Chunk ids are ``<evidence-id-prefix>#<n>``
so every citation in a generated answer maps back to a specific Evidence row and its SourceDocument.
"""

from __future__ import annotations

import numpy as np
from sqlalchemy import select

from sentinel.db.base import Database
from sentinel.db.models import ChunkRecord, Evidence
from sentinel.domain import Chunk, Document
from sentinel.embeddings.base import Embedder
from sentinel.ingestion.chunking import chunk_document
from sentinel.rag.bm25 import BM25Index
from sentinel.rag.retriever import HybridRetriever
from sentinel.vectorstore.base import InMemoryVectorStore

CITATION_PREFIX_LEN = 12


class CaseIndex:
    def __init__(self, db: Database, embedder: Embedder, chunk_size: int = 900, overlap: int = 120) -> None:
        self.db = db
        self.embedder = embedder
        self.chunk_size = chunk_size
        self.overlap = overlap

    def build(self, case_id: str) -> int:
        """Index any evidence not yet chunked (idempotent). Returns number of new chunks."""
        with self.db.session() as s:
            done = set(s.scalars(select(ChunkRecord.evidence_id).where(ChunkRecord.case_id == case_id).distinct()))
            pending = [e for e in s.scalars(select(Evidence).where(Evidence.case_id == case_id)) if e.id not in done]
            docs = [(e.id, e.kind, e.source_id, e.title, e.content) for e in pending]
        rows = []
        for ev_id, kind, source_id, title, content in docs:
            doc = Document(
                ev_id[:CITATION_PREFIX_LEN],
                title,
                content,
                {"kind": kind, "source_id": source_id, "evidence_id": ev_id},
            )
            chunks = chunk_document(doc, self.chunk_size, self.overlap)
            if not chunks:
                continue
            vecs = self.embedder.embed([c.text for c in chunks])
            for c, v in zip(chunks, vecs, strict=True):
                rows.append(
                    ChunkRecord(
                        id=f"{case_id[-8:]}:{c.id}",
                        case_id=case_id,
                        evidence_id=ev_id,
                        text=c.text,
                        embedding=np.asarray(v, dtype=np.float32).tobytes(),
                        meta={**c.metadata, "chunk_id": c.id},
                    )
                )
        if rows:
            with self.db.session() as s:
                s.add_all(rows)
        return len(rows)

    def chunks(self, case_id: str) -> tuple[list[Chunk], np.ndarray]:
        with self.db.session() as s:
            recs = list(s.scalars(select(ChunkRecord).where(ChunkRecord.case_id == case_id).order_by(ChunkRecord.id)))
        chunks = [Chunk(r.meta["chunk_id"], r.evidence_id, r.text, {**r.meta}) for r in recs]
        vecs = (
            np.vstack([np.frombuffer(r.embedding, dtype=np.float32) for r in recs])
            if recs
            else np.zeros((0, self.embedder.dim), dtype=np.float32)
        )
        return chunks, vecs

    def retriever(self, case_id: str) -> HybridRetriever:
        chunks, vecs = self.chunks(case_id)
        store = InMemoryVectorStore(self.embedder.dim)
        bm25 = BM25Index()
        if chunks:
            store.upsert(chunks, vecs)
            bm25.add(chunks)
        return HybridRetriever(self.embedder, store, bm25)

    def evidence_for_citation(self, case_id: str, citation: str) -> str | None:
        prefix = citation.split("#")[0]
        with self.db.session() as s:
            ids = list(
                s.scalars(select(Evidence.id).where(Evidence.case_id == case_id, Evidence.id.like(f"{prefix}%")))
            )
        return ids[0] if len(ids) == 1 else None
