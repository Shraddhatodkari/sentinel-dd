"""Composition root / dependency-injection container.

All infrastructure is wired here from :class:`Settings`, so every layer depends only on interfaces and tests can
substitute any component (HTTP transport, LLM, database) in one place.
"""

from __future__ import annotations

import contextlib
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from sentinel.config import Settings, get_settings
from sentinel.db.base import Database
from sentinel.db.models import RevokedToken
from sentinel.embeddings.base import Embedder, build_embedder
from sentinel.governance.policy import PolicyEngine
from sentinel.ingestion.reference import ReferenceDataService, sync_source_catalog
from sentinel.investigation.resolution import EntityResolver
from sentinel.llm.base import LLMClient
from sentinel.llm.metering import MeteredLLM
from sentinel.llm.router import build_llm
from sentinel.ml.registry import ModelRegistry, RegistryError
from sentinel.observability.logging import get_logger, log_event
from sentinel.rag.case_index import CaseIndex
from sentinel.rag.grounding import GroundingChecker
from sentinel.risk.peer_model import MODEL_NAME, PeerAnomalyModel
from sentinel.screening import ScreeningIndex
from sentinel.security.audit import AuditLog
from sentinel.security.auth import TokenService
from sentinel.security.dlp import DLPEngine
from sentinel.security.guardrails import Guardrails
from sentinel.sources.edgar import EdgarConnector
from sentinel.sources.fetcher import SourceFetcher
from sentinel.sources.gleif import GleifConnector
from sentinel.sources.http import ResilientHttpClient
from sentinel.sources.media import DojConnector, GdeltConnector
from sentinel.sources.raw_store import RawStore
from sentinel.timeutil import utcnow

log = get_logger(__name__)


@dataclass
class Container:
    settings: Settings
    db: Database
    http: ResilientHttpClient
    fetcher: SourceFetcher
    edgar: EdgarConnector
    gleif: GleifConnector
    doj: DojConnector
    gdelt: GdeltConnector | None
    resolver: EntityResolver
    screening: ScreeningIndex
    reference: ReferenceDataService
    embedder: Embedder
    case_index: CaseIndex
    llm: LLMClient
    dlp: DLPEngine
    guardrails: Guardrails
    grounding: GroundingChecker
    policy: PolicyEngine
    audit: AuditLog
    tokens: TokenService
    registry: ModelRegistry
    checkpointer: Any
    peer_model: PeerAnomalyModel | None = None
    peer_model_version: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        llm: LLMClient | None = None,
        migrate: bool | None = None,
    ) -> Container:
        s = settings or get_settings()
        problems = s.validate_for_production()
        if problems:
            raise RuntimeError("refusing to start with insecure production configuration: " + "; ".join(problems))
        s.data_dir.mkdir(parents=True, exist_ok=True)
        db = Database(s.database_url, echo=s.db_echo)
        if s.migrate_on_start if migrate is None else migrate:
            db.upgrade()
        sync_source_catalog(
            db,
            s.enable_opensanctions_peps,
            sla_overrides={"sanctions": s.sanctions_max_age_h, "pep": s.pep_max_age_h},
        )
        http = ResilientHttpClient(
            s.sec_user_agent,
            s.http_timeout_s,
            s.http_max_retries,
            transport=transport,
            backoff_base_s=s.http_backoff_base_s,
            rate_limits_enabled=s.http_rate_limits_enabled,
        )
        fetcher = SourceFetcher(db, http, RawStore(s.raw_store_dir), s.source_cache_ttl_s)
        edgar, gleif = EdgarConnector(fetcher), GleifConnector(fetcher)
        embedder = build_embedder(s.embedding_backend, s.embedding_dim)
        dlp = DLPEngine(secret=s.jwt_secret.get_secret_value())
        audit = AuditLog(db)

        def is_revoked(jti: str) -> bool:
            with db.session() as sess:
                return sess.get(RevokedToken, jti) is not None

        registry = ModelRegistry(s.model_dir, s.mlflow_tracking_uri)
        c = cls(
            settings=s,
            db=db,
            http=http,
            fetcher=fetcher,
            edgar=edgar,
            gleif=gleif,
            doj=DojConnector(fetcher),
            gdelt=GdeltConnector(fetcher, s.gdelt_timespan) if s.enable_gdelt else None,
            resolver=EntityResolver(db, edgar, gleif),
            screening=ScreeningIndex(db),
            reference=ReferenceDataService(db, fetcher, audit=audit),
            embedder=embedder,
            case_index=CaseIndex(db, embedder, s.chunk_size, s.chunk_overlap),
            llm=MeteredLLM(llm or build_llm(s), db, s.llm_price_per_mtok, s.llm_max_tokens),
            dlp=dlp,
            guardrails=Guardrails(dlp),
            grounding=GroundingChecker(s.grounding_threshold),
            policy=PolicyEngine(s.policy_version, s.auto_clear_enabled, min_grounding=s.grounding_threshold),
            audit=audit,
            tokens=TokenService(
                s.jwt_secret.get_secret_value(), s.jwt_algorithm, s.jwt_ttl_minutes, s.jwt_issuer, is_revoked
            ),
            registry=registry,
            checkpointer=build_checkpointer(s.checkpoint_url),
        )
        c.reload_peer_model()
        return c

    def close(self) -> None:
        """Release external resources (checkpointer connection, HTTP pool, DB connection pool). Idempotent."""
        conn = getattr(self.checkpointer, "conn", None)
        if conn is not None:
            with contextlib.suppress(Exception):  # best effort on shutdown
                conn.close()
        self.http.close()
        self.db.engine.dispose()

    def reload_peer_model(self) -> None:
        try:
            model, meta = self.registry.load(MODEL_NAME, stage="production")
            self.peer_model, self.peer_model_version = model, f"{MODEL_NAME}:v{meta['version']}"
        except RegistryError:
            self.peer_model, self.peer_model_version = None, None
            log_event(log, "peer_model_unavailable", detail="train with `sentinel ml train`")

    def revoke_token(self, jti: str, subject: str, expires_at: int) -> None:
        from datetime import UTC, datetime

        with self.db.session() as sess:
            if sess.get(RevokedToken, jti) is None:
                sess.add(RevokedToken(jti=jti, subject=subject, expires_at=datetime.fromtimestamp(expires_at, UTC)))

    def purge_expired_revocations(self) -> int:
        with self.db.session() as sess:
            rows = list(sess.scalars(select(RevokedToken).where(RevokedToken.expires_at < utcnow())))
            for r in rows:
                sess.delete(r)
            return len(rows)


def build_checkpointer(url: str) -> Any:
    """LangGraph checkpointer: SQLite file (single node) or PostgreSQL (multi-replica) for durable HITL pauses."""
    if url.startswith(("postgres://", "postgresql://")):  # pragma: no cover - requires a PostgreSQL server
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg import Connection

        conn = Connection.connect(url, autocommit=True, prepare_threshold=0)
        saver = PostgresSaver(conn)
        saver.setup()
        return saver
    from langgraph.checkpoint.sqlite import SqliteSaver

    if url != ":memory:":
        Path(url).parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(url, check_same_thread=False))
