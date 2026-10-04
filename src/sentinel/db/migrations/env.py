"""Alembic environment. Uses the engine passed by ``sentinel.db.migrate`` when available."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from sentinel.db import models  # noqa: F401 - register tables on Base.metadata
from sentinel.db.base import Base

config = context.config
target_metadata = Base.metadata


def run_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    engine = config.attributes.get("engine")
    if engine is None:
        engine = engine_from_config(
            config.get_section(config.config_ini_section) or {}, prefix="sqlalchemy.", poolclass=pool.NullPool
        )
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True, compare_type=True
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
