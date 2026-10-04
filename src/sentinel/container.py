"""Composition root / dependency-injection container.

All infrastructure is wired here from :class:`Settings`, so every layer depends only on
interfaces and tests can swap any component (LLM, vector DB, model bundle) in one place.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from sentinel.config import Settings, get_settings
from sentinel.embeddings.base import Embedder, build_embedder
from sentinel.governance.hitl import ApprovalQueue
from sentinel.governance.policy import PolicyEngine
from sentinel.graphrag.graph import KnowledgeGraph
from sentinel.ingestion.loaders import load_directory
from sentinel.ingestion.pipeline import IngestionPipeline
from sentinel.llm.base import LLMClient
from sentinel.llm.router import build_llm
from sentinel.memory.memory import LongTermMemory
from sentinel.ml.models import TrainedBundle, train_all
from sentinel.ml.registry import ModelRegistry, RegistryError
from sentinel.ml.scoring import RiskEngine
from sentinel.observability.logging import get_logger, log_event
from sentinel.rag.bm25 import BM25Index
from sentinel.rag.grounding import GroundingChecker
from sentinel.rag.qa import GroundedQA
from sentinel.rag.retriever import HybridRetriever
from sentinel.screening import Screener
from sentinel.security.audit import AuditLog
from sentinel.security.auth import TokenService
from sentinel.security.dlp import DLPEngine
from sentinel.security.guardrails import Guardrails
from sentinel.structured import EntityRegistry
from sentinel.vectorstore.base import InMemoryVectorStore, VectorStore

log = get_logger(__name__)


def _data_dir() -> Path:
    """Repo checkout -> ./data next to src/; installed wheel/container -> $SENTINEL_DATA_DIR or ./data."""
    env = os.environ.get("SENTINEL_DATA_DIR")
    if env:
        return Path(env)
    repo = Path(__file__).resolve().parents[2] / "data"
    return repo if repo.exists() else Path.cwd() / "data"


DATA_DIR = _data_dir()


def build_vector_store(settings: Settings, dim: int) -> VectorStore:
    if settings.vector_backend == "qdrant":  # pragma: no cover - needs running Qdrant
        from sentinel.vectorstore.qdrant_store import QdrantVectorStore

        return QdrantVectorStore(settings.qdrant_url, "sentinel_chunks", dim)
    if settings.vector_backend == "pgvector":  # pragma: no cover - needs running Postgres
        from sentinel.vectorstore.pgvector_store import PgVectorStore

        return PgVectorStore(settings.postgres_dsn, dim)
    return InMemoryVectorStore(dim)


def load_or_train_bundle(settings: Settings, n: int = 4000) -> tuple[TrainedBundle, str]:
    reg = ModelRegistry(settings.model_dir, settings.mlflow_tracking_uri)
    try:
        payload, meta = reg.load("risk_bundle", stage="production")
        return payload["bundle"], f"risk_bundle:v{meta['version']}"
    except RegistryError:
        log_event(log, "no_production_model_training_fallback")
        bundle, _ = train_all(n)
        return bundle, "risk_bundle:ephemeral"


@dataclass
class Container:
    settings: Settings
    llm: LLMClient
    embedder: Embedder
    store: VectorStore
    bm25: BM25Index
    graph: KnowledgeGraph
    dlp: DLPEngine
    guardrails: Guardrails
    retriever: HybridRetriever
    qa: GroundedQA
    ingestion: IngestionPipeline
    screener: Screener
    entities: EntityRegistry
    risk: RiskEngine
    policy: PolicyEngine
    audit: AuditLog
    approvals: ApprovalQueue
    memory: LongTermMemory
    tokens: TokenService
    extras: dict[str, object] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        settings: Settings | None = None,
        *,
        llm: LLMClient | None = None,
        bundle: TrainedBundle | None = None,
        bootstrap_data: bool = True,
        audit_path: str | None = None,
    ) -> Container:
        s = settings or get_settings()
        llm = llm or build_llm(s)
        embedder = build_embedder(s.embedding_backend, s.embedding_dim)
        store = build_vector_store(s, embedder.dim)
        bm25 = BM25Index()
        graph = KnowledgeGraph()
        dlp = DLPEngine(secret=s.jwt_secret.get_secret_value())
        guard = Guardrails(dlp)
        retriever = HybridRetriever(embedder, store, bm25)
        qa = GroundedQA(retriever, llm, guard, GroundingChecker())
        ingestion = IngestionPipeline(
            embedder, store, bm25, graph, dlp, chunk_size=s.chunk_size, overlap=s.chunk_overlap
        )
        if bundle is None:
            bundle, version = load_or_train_bundle(s)
        else:
            version = "risk_bundle:injected"
        audit = AuditLog(audit_path)
        c = cls(
            settings=s,
            llm=llm,
            embedder=embedder,
            store=store,
            bm25=bm25,
            graph=graph,
            dlp=dlp,
            guardrails=guard,
            retriever=retriever,
            qa=qa,
            ingestion=ingestion,
            screener=Screener.from_json(DATA_DIR / "watchlist.json"),
            entities=EntityRegistry.from_json(DATA_DIR / "entity_registry.json"),
            risk=RiskEngine(bundle, version),
            policy=PolicyEngine(s.hitl_risk_threshold, s.grounding_threshold),
            audit=audit,
            approvals=ApprovalQueue(audit),
            memory=LongTermMemory(embedder),
            tokens=TokenService(s.jwt_secret.get_secret_value(), s.jwt_algorithm, s.jwt_ttl_minutes),
        )
        if bootstrap_data:
            c.ingest_directory(DATA_DIR / "sample_docs")
        return c

    def ingest_directory(self, path: str | Path) -> dict[str, object]:
        rep = self.ingestion.ingest(list(load_directory(path)))
        self.retriever.known_entities = sorted(self.ingestion.entities | set(self.entities.names()))
        return rep.__dict__
