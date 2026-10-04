"""Programmatic Alembic runner (used by ``sentinel db upgrade``, app start-up checks and tests)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config(engine: Engine) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", engine.url.render_as_string(hide_password=False).replace("%", "%%"))
    cfg.attributes["engine"] = engine
    return cfg


def upgrade_to_head(engine: Engine) -> None:
    command.upgrade(alembic_config(engine), "head")


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def head_revision(engine: Engine) -> str | None:
    return ScriptDirectory.from_config(alembic_config(engine)).get_current_head()
