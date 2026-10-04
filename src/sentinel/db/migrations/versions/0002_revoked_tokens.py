"""JWT revocation list shared across API replicas

Revision ID: 0002_revoked_tokens
Revises: 0001_initial
Create Date: 2026-10-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_revoked_tokens"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "revoked_tokens",
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("jti"),
    )


def downgrade() -> None:
    op.drop_table("revoked_tokens")
