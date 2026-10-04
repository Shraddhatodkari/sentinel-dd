"""PostgreSQL + pgvector store (transactional, row-level security friendly, HNSW index)."""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from sentinel.domain import Chunk, ScoredChunk
from sentinel.vectorstore.base import MetadataFilter, VectorStore, matches

DDL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS chunks (
  id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL,
  text TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{{}}',
  embedding vector({dim}) NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_doc_idx ON chunks (doc_id);
CREATE INDEX IF NOT EXISTS chunks_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
"""


class PgVectorStore(VectorStore):
    def __init__(self, dsn: str, dim: int, conn: Any | None = None) -> None:
        import psycopg
        from pgvector.psycopg import register_vector

        self.dim = dim
        self._conn = conn or psycopg.connect(dsn, autocommit=True)
        self._conn.execute(DDL.format(dim=dim))
        register_vector(self._conn)

    def upsert(self, chunks: list[Chunk], vectors: np.ndarray) -> None:
        with self._conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (id, doc_id, text, metadata, embedding) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (id) DO UPDATE SET text=EXCLUDED.text, metadata=EXCLUDED.metadata, "
                "embedding=EXCLUDED.embedding",
                [(c.id, c.doc_id, c.text, json.dumps(c.metadata), v) for c, v in zip(chunks, vectors, strict=True)],
            )

    def search(self, vector: np.ndarray, k: int = 5, flt: MetadataFilter | None = None) -> list[ScoredChunk]:
        # over-fetch then apply rich filter semantics in Python (simple JSONB equality pushed down)
        eq = {kk: v for kk, v in (flt or {}).items() if not isinstance(v, dict | list | tuple | set)}
        rows = self._conn.execute(
            "SELECT id, doc_id, text, metadata, 1 - (embedding <=> %s) AS score FROM chunks "
            "WHERE metadata @> %s::jsonb ORDER BY embedding <=> %s LIMIT %s",
            (vector, json.dumps(eq), vector, k * 4),
        ).fetchall()
        out = [ScoredChunk(Chunk(r[0], r[1], r[2], r[3]), float(r[4]), "dense") for r in rows if matches(r[3], flt)]
        return out[:k]

    def delete_document(self, doc_id: str) -> int:
        return self._conn.execute("DELETE FROM chunks WHERE doc_id = %s", (doc_id,)).rowcount

    def count(self) -> int:
        return int(self._conn.execute("SELECT count(*) FROM chunks").fetchone()[0])

    def all_chunks(self) -> list[Chunk]:
        rows = self._conn.execute("SELECT id, doc_id, text, metadata FROM chunks").fetchall()
        return [Chunk(r[0], r[1], r[2], r[3]) for r in rows]
