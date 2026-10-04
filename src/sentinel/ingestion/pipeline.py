"""Ingestion pipeline: load -> DLP redact -> chunk -> embed -> index (dense + sparse) -> graph.

Idempotent: re-ingesting a document replaces its previous chunks (content-hash versioned).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from sentinel.domain import Document
from sentinel.embeddings.base import Embedder
from sentinel.graphrag.extractor import CompositeExtractor, PatternExtractor
from sentinel.graphrag.graph import KnowledgeGraph
from sentinel.ingestion.chunking import chunk_document
from sentinel.observability.logging import get_logger, log_event
from sentinel.observability.metrics import METRICS
from sentinel.rag.bm25 import BM25Index
from sentinel.security.dlp import DLPEngine
from sentinel.vectorstore.base import VectorStore

log = get_logger(__name__)


@dataclass
class IngestReport:
    documents: int = 0
    chunks: int = 0
    skipped_unchanged: int = 0
    pii_findings: dict[str, int] = field(default_factory=dict)
    graph: dict[str, int] = field(default_factory=dict)


class IngestionPipeline:
    def __init__(
        self,
        embedder: Embedder,
        store: VectorStore,
        bm25: BM25Index,
        graph: KnowledgeGraph,
        dlp: DLPEngine,
        extractor: CompositeExtractor | None = None,
        chunk_size: int = 800,
        overlap: int = 120,
        batch_size: int = 64,
    ) -> None:
        self.embedder = embedder
        self.store = store
        self.bm25 = bm25
        self.graph = graph
        self.dlp = dlp
        self.extractor = extractor or CompositeExtractor(PatternExtractor())
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.batch_size = batch_size
        self._hashes: dict[str, str] = {}
        self.entities: set[str] = set()

    def ingest(self, docs: list[Document]) -> IngestReport:
        rep = IngestReport()
        for doc in docs:
            digest = hashlib.sha256((doc.title + doc.text).encode()).hexdigest()
            if self._hashes.get(doc.id) == digest:
                rep.skipped_unchanged += 1
                continue
            # 1) DLP before anything is persisted or sent to an embedding API
            red = self.dlp.redact(doc.text, strategy="tokenize")
            for f in red.findings:
                rep.pii_findings[f.label] = rep.pii_findings.get(f.label, 0) + 1
            clean = Document(doc.id, doc.title, red.text, {**doc.metadata, "content_hash": digest[:16]})
            # 2) replace previous version
            self.store.delete_document(doc.id)
            self.bm25.delete_document(doc.id)
            # 3) chunk + embed in batches
            chunks = chunk_document(clean, self.chunk_size, self.overlap)
            for i in range(0, len(chunks), self.batch_size):
                batch = chunks[i : i + self.batch_size]
                self.store.upsert(batch, self.embedder.embed([c.text for c in batch]))
            self.bm25.add(chunks)
            # 4) knowledge graph
            for ch in chunks:
                self.graph.add(self.extractor.extract(ch))
            if ent := doc.metadata.get("entity"):
                self.entities.add(str(ent))
                self.graph.resolve(str(ent))
            self._hashes[doc.id] = digest
            rep.documents += 1
            rep.chunks += len(chunks)
        rep.graph = self.graph.stats()
        METRICS.inc("ingested_documents", rep.documents)
        log_event(
            log, "ingestion_complete", documents=rep.documents, chunks=rep.chunks, pii=rep.pii_findings, graph=rep.graph
        )
        return rep
