"""Engine / session management and transaction boundaries.

* SQLite (dev/test) with WAL + foreign keys; PostgreSQL (prod) via ``postgresql+psycopg://``.
* ``Database.session()`` is a unit of work: commit on success, rollback on any exception.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=10000")
    cur.close()


class Database:
    def __init__(self, url: str, echo: bool = False) -> None:
        self.url = url
        kwargs: dict[str, Any] = {"echo": echo, "future": True}
        if url.startswith("sqlite"):
            path = url.split("///", 1)[-1]
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        else:
            kwargs.update(pool_size=10, max_overflow=20, pool_pre_ping=True, pool_recycle=1800)
        self.engine: Engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", _sqlite_pragmas)
        self._factory = sessionmaker(self.engine, expire_on_commit=False)

    @property
    def is_sqlite(self) -> bool:
        return self.url.startswith("sqlite")

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def upgrade(self) -> None:
        """Apply Alembic migrations to head (the only supported way to create/alter the schema)."""
        from sentinel.db.migrate import upgrade_to_head

        upgrade_to_head(self.engine)

    def ping(self) -> bool:
        from sqlalchemy import text

        try:
            with self.engine.connect() as c:
                c.execute(text("SELECT 1"))
            return True
        except Exception:
            return False
