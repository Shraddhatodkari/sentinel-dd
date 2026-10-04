"""Tamper-evident, database-persisted audit trail.

* Append-only: UPDATE/DELETE are rejected by database triggers (see migration 0001).
* Hash-chained: each event stores SHA-256(prev_hash ‖ canonical JSON of the event).
* Serialised writers: the single-row ``audit_head`` is locked (``SELECT … FOR UPDATE`` on PostgreSQL) so
  concurrent replicas cannot fork the chain; SQLite relies on its database-level write lock + retry.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from sentinel.db.base import Database
from sentinel.db.models import AuditEvent, AuditHead
from sentinel.observability.logging import request_id_ctx
from sentinel.timeutil import iso, utcnow

GENESIS = "0" * 64


def _digest(prev_hash: str, body: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + json.dumps(body, sort_keys=True, default=str)).encode()).hexdigest()


def _body(e: AuditEvent | dict[str, Any]) -> dict[str, Any]:
    g = (lambda k: e[k]) if isinstance(e, dict) else (lambda k: getattr(e, k))
    return {
        "seq": g("seq"),
        "ts": iso(g("ts")),
        "tenant": g("tenant"),
        "actor": g("actor"),
        "actor_roles": list(g("actor_roles") or []),
        "action": g("action"),
        "resource_type": g("resource_type"),
        "resource_id": g("resource_id"),
        "details": g("details") or {},
        "request_id": g("request_id"),
    }


class AuditLog:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._lock = threading.Lock()

    def append(
        self,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: dict[str, Any] | None = None,
        *,
        roles: list[str] | None = None,
        tenant: str = "default",
    ) -> dict[str, Any]:
        for attempt in range(8):
            try:
                with self._lock, self.db.session() as s:
                    head = s.execute(select(AuditHead).where(AuditHead.id == 1).with_for_update()).scalar_one()
                    ts: datetime = utcnow()
                    rec: dict[str, Any] = {
                        "seq": head.last_seq + 1,
                        "ts": ts,
                        "tenant": tenant,
                        "actor": actor,
                        "actor_roles": roles or [],
                        "action": action,
                        "resource_type": resource_type,
                        "resource_id": str(resource_id),
                        "details": details or {},
                        "request_id": request_id_ctx.get(),
                    }
                    prev = head.last_hash
                    h = _digest(prev, _body(rec))
                    s.add(AuditEvent(**rec, prev_hash=prev, hash=h))
                    head.last_seq, head.last_hash = rec["seq"], h
                    return {**_body(rec), "prev_hash": prev, "hash": h}
            except (IntegrityError, OperationalError):
                if attempt == 7:
                    raise
        raise RuntimeError("unreachable")

    def records(
        self,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        tenant: str | None = None,
        limit: int = 500,
        before_seq: int | None = None,
    ) -> list[dict[str, Any]]:
        with self.db.session() as s:
            q = select(AuditEvent)
            if resource_type:
                q = q.where(AuditEvent.resource_type == resource_type)
            if resource_id:
                q = q.where(AuditEvent.resource_id == resource_id)
            if tenant:
                q = q.where(AuditEvent.tenant == tenant)
            if before_seq is not None:
                q = q.where(AuditEvent.seq < before_seq)
            rows = s.scalars(q.order_by(AuditEvent.seq.desc()).limit(limit)).all()
            return [{**_body(r), "prev_hash": r.prev_hash, "hash": r.hash} for r in rows]

    def count(self) -> int:
        with self.db.session() as s:
            return int(s.scalar(select(func.count()).select_from(AuditEvent)) or 0)

    def verify(self, batch: int = 5000) -> dict[str, Any]:
        """Recompute the whole chain. Returns {intact, records, first_bad_seq, head_matches}."""
        prev, n, last_seq = GENESIS, 0, -1
        with self.db.session() as s:
            while True:
                rows = s.scalars(
                    select(AuditEvent).where(AuditEvent.seq > last_seq).order_by(AuditEvent.seq).limit(batch)
                ).all()
                if not rows:
                    break
                for r in rows:
                    if r.seq != last_seq + 1 or r.prev_hash != prev or _digest(prev, _body(r)) != r.hash:
                        return {"intact": False, "records": n, "first_bad_seq": r.seq, "head_matches": False}
                    prev, last_seq, n = r.hash, r.seq, n + 1
            head = s.get(AuditHead, 1)
            head_ok = head is not None and head.last_hash == prev and head.last_seq == last_seq
        return {
            "intact": head_ok,
            "records": n,
            "first_bad_seq": None if head_ok else last_seq + 1,
            "head_matches": head_ok,
        }
