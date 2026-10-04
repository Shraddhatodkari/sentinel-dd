"""initial schema (sources, lineage, watch-lists, entities, cases, evidence, signals, assessments,
policy decisions, human reviews, LLM usage and append-only hash-chained audit)

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("seq", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("actor_roles", sa.JSON(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("seq"),
        sa.UniqueConstraint("hash"),
    )
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.create_index("ix_audit_resource", ["resource_type", "resource_id"], unique=False)
        batch_op.create_index("ix_audit_tenant", ["tenant"], unique=False)

    op.create_table(
        "audit_head",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False),
        sa.Column("last_hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "entities",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("canonical_name", sa.String(length=500), nullable=False),
        sa.Column("entity_type", sa.String(length=16), nullable=False),
        sa.Column("jurisdiction", sa.String(length=32), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=True),
        sa.Column("purpose", sa.String(length=48), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("llm_usage", schema=None) as batch_op:
        batch_op.create_index("ix_llm_usage_case", ["case_id"], unique=False)

    op.create_table(
        "sources",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("authority", sa.String(length=200), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("license", sa.String(length=300), nullable=False),
        sa.Column("freshness_sla_h", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("tenant", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("failed_logins", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_table(
        "cases",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tenant", sa.String(length=64), nullable=False),
        sa.Column("entity_query", sa.String(length=300), nullable=False),
        sa.Column("identifier_hint", sa.JSON(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.String(length=32), nullable=True),
        sa.Column("resolved_name", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("risk_band", sa.String(length=16), nullable=True),
        sa.Column("risk_score", sa.Float(), nullable=True),
        sa.Column("policy_outcome", sa.String(length=24), nullable=True),
        sa.Column("report", sa.Text(), nullable=False),
        sa.Column("report_grounding", sa.Float(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','running','awaiting_review','approved','rejected','cleared','failed')",
            name="ck_case_status",
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant", "created_by", "idempotency_key", name="uq_case_idempotency"),
    )
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.create_index("ix_case_created_by", ["created_by"], unique=False)
        batch_op.create_index("ix_case_tenant_status", ["tenant", "status"], unique=False)

    op.create_table(
        "entity_identifiers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entity_id", sa.String(length=32), nullable=False),
        sa.Column("scheme", sa.String(length=16), nullable=False),
        sa.Column("value", sa.String(length=64), nullable=False),
        sa.Column("evidence_id", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scheme", "value", name="uq_entity_identifier"),
    )
    op.create_table(
        "source_documents",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "url", "sha256", name="uq_srcdoc_version"),
    )
    with op.batch_alter_table("source_documents", schema=None) as batch_op:
        batch_op.create_index("ix_srcdoc_source_retrieved", ["source_id", "retrieved_at"], unique=False)
        batch_op.create_index("ix_srcdoc_url", ["url"], unique=False)

    op.create_table(
        "case_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=False),
        sa.Column("node", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("case_events", schema=None) as batch_op:
        batch_op.create_index("ix_case_events_case", ["case_id", "started_at"], unique=False)

    op.create_table(
        "evidence",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=False),
        sa.Column("document_id", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("structured", sa.JSON(), nullable=False),
        sa.Column("locator", sa.String(length=500), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("quality", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_evidence_confidence"),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["source_documents.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "content_hash", name="uq_evidence_case_hash"),
    )
    with op.batch_alter_table("evidence", schema=None) as batch_op:
        batch_op.create_index("ix_evidence_case_kind", ["case_id", "kind"], unique=False)

    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("document_id", sa.String(length=32), nullable=True),
        sa.Column("records_in", sa.Integer(), nullable=False),
        sa.Column("records_loaded", sa.Integer(), nullable=False),
        sa.Column("records_rejected", sa.Integer(), nullable=False),
        sa.Column("unchanged", sa.Boolean(), nullable=False),
        sa.Column("quality", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("triggered_by", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["source_documents.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("ingestion_runs", schema=None) as batch_op:
        batch_op.create_index("ix_ingest_source_started", ["source_id", "started_at"], unique=False)

    op.create_table(
        "policy_decisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=False),
        sa.Column("policy_version", sa.String(length=48), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("required_role", sa.String(length=32), nullable=True),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id"),
    )
    op.create_table(
        "reviews",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=False),
        sa.Column("tenant", sa.String(length=64), nullable=False),
        sa.Column("tier", sa.String(length=24), nullable=False),
        sa.Column("required_role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_by", sa.String(length=64), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
        sa.Column("decided_role", sa.String(length=32), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('pending','approved','rejected')", name="ck_review_status"),
        sa.CheckConstraint("decided_by IS NULL OR decided_by <> requested_by", name="ck_review_maker_checker"),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id"),
    )
    with op.batch_alter_table("reviews", schema=None) as batch_op:
        batch_op.create_index("ix_reviews_status", ["status"], unique=False)

    op.create_table(
        "risk_assessments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=False),
        sa.Column("scorecard_version", sa.String(length=48), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("band", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("factors", sa.JSON(), nullable=False),
        sa.Column("features", sa.JSON(), nullable=False),
        sa.Column("model_versions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id"),
    )
    op.create_table(
        "signals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=False),
        sa.Column("signal_type", sa.String(length=48), nullable=False),
        sa.Column("label", sa.String(length=500), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("derived_by", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("signals", schema=None) as batch_op:
        batch_op.create_index("ix_signals_case", ["case_id"], unique=False)

    op.create_table(
        "watchlist_entries",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("entity_type", sa.String(length=16), nullable=False),
        sa.Column("primary_name", sa.String(length=500), nullable=False),
        sa.Column("programs", sa.JSON(), nullable=False),
        sa.Column("countries", sa.JSON(), nullable=False),
        sa.Column("identifiers", sa.JSON(), nullable=False),
        sa.Column("remarks", sa.Text(), nullable=False),
        sa.Column("listed_on", sa.String(length=32), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["source_documents.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "external_id", "document_id", name="uq_wl_entry_version"),
    )
    with op.batch_alter_table("watchlist_entries", schema=None) as batch_op:
        batch_op.create_index("ix_wl_current", ["source_id", "is_current"], unique=False)

    op.create_table(
        "chunks",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=False),
        sa.Column("evidence_id", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("chunks", schema=None) as batch_op:
        batch_op.create_index("ix_chunks_case", ["case_id"], unique=False)

    op.create_table(
        "relationships",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("relation", sa.String(length=32), nullable=False),
        sa.Column("object", sa.String(length=500), nullable=False),
        sa.Column("object_type", sa.String(length=16), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("evidence_id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("relationships", schema=None) as batch_op:
        batch_op.create_index("ix_rel_case", ["case_id"], unique=False)

    op.create_table(
        "watchlist_names",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entry_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("normalized", sa.String(length=500), nullable=False),
        sa.Column("name_type", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(["entry_id"], ["watchlist_entries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("watchlist_names", schema=None) as batch_op:
        batch_op.create_index("ix_wl_name_norm", ["normalized"], unique=False)

    # ---- chain head for the audit log
    op.execute(sa.text("INSERT INTO audit_head (id, last_seq, last_hash) VALUES (1, -1, :h)").bindparams(h="0" * 64))
    # ---- database-enforced immutability of the audit trail
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE OR REPLACE FUNCTION sentinel_audit_immutable() RETURNS trigger AS $$ "
            "BEGIN RAISE EXCEPTION 'audit_events is append-only'; END; $$ LANGUAGE plpgsql"
        )
        op.execute(
            "CREATE TRIGGER audit_events_immutable BEFORE UPDATE OR DELETE ON audit_events "
            "FOR EACH ROW EXECUTE FUNCTION sentinel_audit_immutable()"
        )
    else:
        op.execute(
            "CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events "
            "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END"
        )
        op.execute(
            "CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events "
            "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_events_immutable ON audit_events")
        op.execute("DROP FUNCTION IF EXISTS sentinel_audit_immutable()")
    else:
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_update")
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_delete")
    with op.batch_alter_table("watchlist_names", schema=None) as batch_op:
        batch_op.drop_index("ix_wl_name_norm")

    op.drop_table("watchlist_names")
    with op.batch_alter_table("relationships", schema=None) as batch_op:
        batch_op.drop_index("ix_rel_case")

    op.drop_table("relationships")
    with op.batch_alter_table("chunks", schema=None) as batch_op:
        batch_op.drop_index("ix_chunks_case")

    op.drop_table("chunks")
    with op.batch_alter_table("watchlist_entries", schema=None) as batch_op:
        batch_op.drop_index("ix_wl_current")

    op.drop_table("watchlist_entries")
    with op.batch_alter_table("signals", schema=None) as batch_op:
        batch_op.drop_index("ix_signals_case")

    op.drop_table("signals")
    op.drop_table("risk_assessments")
    with op.batch_alter_table("reviews", schema=None) as batch_op:
        batch_op.drop_index("ix_reviews_status")

    op.drop_table("reviews")
    op.drop_table("policy_decisions")
    with op.batch_alter_table("ingestion_runs", schema=None) as batch_op:
        batch_op.drop_index("ix_ingest_source_started")

    op.drop_table("ingestion_runs")
    with op.batch_alter_table("evidence", schema=None) as batch_op:
        batch_op.drop_index("ix_evidence_case_kind")

    op.drop_table("evidence")
    with op.batch_alter_table("case_events", schema=None) as batch_op:
        batch_op.drop_index("ix_case_events_case")

    op.drop_table("case_events")
    with op.batch_alter_table("source_documents", schema=None) as batch_op:
        batch_op.drop_index("ix_srcdoc_url")
        batch_op.drop_index("ix_srcdoc_source_retrieved")

    op.drop_table("source_documents")
    op.drop_table("entity_identifiers")
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.drop_index("ix_case_tenant_status")
        batch_op.drop_index("ix_case_created_by")

    op.drop_table("cases")
    op.drop_table("users")
    op.drop_table("sources")
    with op.batch_alter_table("llm_usage", schema=None) as batch_op:
        batch_op.drop_index("ix_llm_usage_case")

    op.drop_table("llm_usage")
    op.drop_table("entities")
    op.drop_table("audit_head")
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_index("ix_audit_tenant")
        batch_op.drop_index("ix_audit_resource")

    op.drop_table("audit_events")
