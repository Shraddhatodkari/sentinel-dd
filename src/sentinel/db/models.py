"""ORM schema.

The schema encodes Sentinel's epistemic layering explicitly — each layer references the one below it:

    SourceDocument  (raw bytes exactly as retrieved, content-addressed, with URL + timestamp)
      └─ Evidence   (a located excerpt / structured fact extracted from one document)
           └─ Signal          (an analytical inference derived from one or more evidence items)
                └─ RiskAssessment   (scorecard calculation over signals, versioned)
                     └─ PolicyDecision  (rule outcome, versioned)
                          └─ Review          (human decision — the only "decision" of record)

Audit events are append-only and SHA-256 hash-chained.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sentinel.db.base import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


TS = DateTime(timezone=True)


# --------------------------------------------------------------------------- identity & access
class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    roles: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    tenant: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, default=utcnow)
    password_changed_at: Mapped[datetime] = mapped_column(TS, nullable=False, default=utcnow)


class RevokedToken(Base):
    """JWT denylist (logout / forced revocation), shared by all API replicas."""

    __tablename__ = "revoked_tokens"
    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    revoked_at: Mapped[datetime] = mapped_column(TS, nullable=False, default=utcnow)


# --------------------------------------------------------------------------- sources & lineage
class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # sanctions|pep|registry|filings|media|enforcement
    authority: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    license: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    freshness_sla_h: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SourceDocument(Base):
    """A raw artefact exactly as retrieved. Bytes live in the content-addressed raw store."""

    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint("source_id", "url", "sha256", name="uq_srcdoc_version"),
        Index("ix_srcdoc_source_retrieved", "source_id", "retrieved_at"),
        Index("ix_srcdoc_url", "url"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(TS, nullable=False, default=utcnow)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    __table_args__ = (Index("ix_ingest_source_started", "source_id", "started_at"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # reference | entity
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(TS, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TS)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("source_documents.id"))
    records_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_loaded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unchanged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quality: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    triggered_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")


# --------------------------------------------------------------------------- reference data: watch-lists
class WatchlistEntry(Base):
    __tablename__ = "watchlist_entries"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", "document_id", name="uq_wl_entry_version"),
        Index("ix_wl_current", "source_id", "is_current"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False)
    document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)  # sanctions | pep
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    primary_name: Mapped[str] = mapped_column(String(500), nullable=False)
    programs: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    countries: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    identifiers: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    remarks: Mapped[str] = mapped_column(Text, nullable=False, default="")
    listed_on: Mapped[str | None] = mapped_column(String(32))
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    names: Mapped[list[WatchlistName]] = relationship(back_populates="entry", cascade="all, delete-orphan")


class WatchlistName(Base):
    __tablename__ = "watchlist_names"
    __table_args__ = (Index("ix_wl_name_norm", "normalized"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[str] = mapped_column(ForeignKey("watchlist_entries.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized: Mapped[str] = mapped_column(String(500), nullable=False)
    name_type: Mapped[str] = mapped_column(String(16), nullable=False, default="primary")  # primary|aka|weak
    entry: Mapped[WatchlistEntry] = relationship(back_populates="names")


# --------------------------------------------------------------------------- entities
class Entity(Base):
    __tablename__ = "entities"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    canonical_name: Mapped[str] = mapped_column(String(500), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False, default="company")
    jurisdiction: Mapped[str | None] = mapped_column(String(32))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, nullable=False, default=utcnow, onupdate=utcnow)
    identifiers: Mapped[list[EntityIdentifier]] = relationship(back_populates="entity", cascade="all, delete-orphan")


class EntityIdentifier(Base):
    __tablename__ = "entity_identifiers"
    __table_args__ = (UniqueConstraint("scheme", "value", name="uq_entity_identifier"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    scheme: Mapped[str] = mapped_column(String(16), nullable=False)  # CIK | LEI | TICKER | EIN
    value: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_id: Mapped[str | None] = mapped_column(String(32))
    entity: Mapped[Entity] = relationship(back_populates="identifiers")


# --------------------------------------------------------------------------- cases
class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint("tenant", "created_by", "idempotency_key", name="uq_case_idempotency"),
        Index("ix_case_tenant_status", "tenant", "status"),
        Index("ix_case_created_by", "created_by"),
        CheckConstraint(
            "status IN ('queued','running','awaiting_review','approved','rejected','cleared','failed')",
            name="ck_case_status",
        ),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    entity_query: Mapped[str] = mapped_column(String(300), nullable=False)
    identifier_hint: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    entity_id: Mapped[str | None] = mapped_column(ForeignKey("entities.id"))
    resolved_name: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, nullable=False, default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(TS)
    completed_at: Mapped[datetime | None] = mapped_column(TS)
    risk_band: Mapped[str | None] = mapped_column(String(16))
    risk_score: Mapped[float | None] = mapped_column(Float)
    policy_outcome: Mapped[str | None] = mapped_column(String(24))
    report: Mapped[str] = mapped_column(Text, nullable=False, default="")
    report_grounding: Mapped[float | None] = mapped_column(Float)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __mapper_args__ = {"version_id_col": version}


class CaseEvent(Base):
    """Agent execution trace (one row per node run) — powers progress, tracing and diagnosis."""

    __tablename__ = "case_events"
    __table_args__ = (Index("ix_case_events_case", "case_id", "started_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=False)
    node: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # completed | failed | degraded
    started_at: Mapped[datetime] = mapped_column(TS, nullable=False, default=utcnow)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("case_id", "content_hash", name="uq_evidence_case_hash"),
        Index("ix_evidence_case_kind", "case_id", "kind"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_evidence_confidence"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    structured: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    locator: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    quality: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class Relationship(Base):
    __tablename__ = "relationships"
    __table_args__ = (Index("ix_rel_case", "case_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    object: Mapped[str] = mapped_column(String(500), nullable=False)
    object_type: Mapped[str] = mapped_column(String(16), nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False)


class ChunkRecord(Base):
    """Retrieval index rows (text + embedding). Source of truth for the vector index."""

    __tablename__ = "chunks"
    __table_args__ = (Index("ix_chunks_case", "case_id"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=False)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (Index("ix_signals_case", "case_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(48), nullable=False)
    label: Mapped[str] = mapped_column(String(500), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)  # info|low|medium|high|critical
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    derived_by: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, unique=True)
    scorecard_version: Mapped[str] = mapped_column(String(48), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    band: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    factors: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    model_versions: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, default=utcnow)


class PolicyDecisionRecord(Base):
    __tablename__ = "policy_decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, unique=True)
    policy_version: Mapped[str] = mapped_column(String(48), nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)  # AUTO_CLEAR | REVIEW | ESCALATE
    required_role: Mapped[str | None] = mapped_column(String(32))
    rules: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, default=utcnow)


class Review(Base):
    """Human decision record (maker-checker)."""

    __tablename__ = "reviews"
    __table_args__ = (
        Index("ix_reviews_status", "status"),
        CheckConstraint("decided_by IS NULL OR decided_by <> requested_by", name="ck_review_maker_checker"),
        CheckConstraint("status IN ('pending','approved','rejected')", name="ck_review_status"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, unique=True)
    tenant: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    tier: Mapped[str] = mapped_column(String(24), nullable=False)  # REVIEW | ESCALATE
    required_role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(TS, nullable=False, default=utcnow)
    reasons: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_role: Mapped[str | None] = mapped_column(String(32))
    decided_at: Mapped[datetime | None] = mapped_column(TS)
    comment: Mapped[str | None] = mapped_column(Text)


class LLMUsage(Base):
    __tablename__ = "llm_usage"
    __table_args__ = (Index("ix_llm_usage_case", "case_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str | None] = mapped_column(String(32))
    purpose: Mapped[str] = mapped_column(String(48), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ts: Mapped[datetime] = mapped_column(TS, nullable=False, default=utcnow)


# --------------------------------------------------------------------------- audit (append-only)
class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_resource", "resource_type", "resource_id"), Index("ix_audit_tenant", "tenant"))
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    ts: Mapped[datetime] = mapped_column(TS, nullable=False)
    tenant: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_roles: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, default="-")
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)


class AuditHead(Base):
    """Single-row chain head; locked during append to serialise writers across replicas."""

    __tablename__ = "audit_head"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=-1)
    last_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="0" * 64)
