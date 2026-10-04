"""Tamper-evident audit trail: append-only, SHA-256 hash-chained JSON lines.

Every record embeds the hash of its predecessor, so any edit/deletion breaks ``verify()``.
Satisfies the "who did what, when, and why" requirement for regulated AI decisioning
(e.g. EU AI Act record-keeping, SOC 2 CC7, RBI/SEBI model-risk guidance).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else None
        self._lock = threading.Lock()
        self._records: list[dict[str, Any]] = []
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if self._path.exists():
                for line in self._path.read_text().splitlines():
                    if line.strip():
                        self._records.append(json.loads(line))

    @staticmethod
    def _hash(record: dict[str, Any]) -> str:
        body = {k: v for k, v in record.items() if k != "hash"}
        return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()

    def append(self, actor: str, action: str, resource: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            prev = self._records[-1]["hash"] if self._records else GENESIS
            rec: dict[str, Any] = {
                "seq": len(self._records),
                "ts": time.time(),
                "actor": actor,
                "action": action,
                "resource": resource,
                "details": details or {},
                "prev_hash": prev,
            }
            rec["hash"] = self._hash(rec)
            self._records.append(rec)
            if self._path:
                with self._path.open("a") as fh:
                    fh.write(json.dumps(rec, default=str) + "\n")
            return rec

    def records(self, resource: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return [r for r in self._records if resource is None or r["resource"] == resource]

    def verify(self) -> tuple[bool, int | None]:
        """Return (ok, first_bad_seq)."""
        prev = GENESIS
        for rec in self._records:
            if rec["prev_hash"] != prev or self._hash(rec) != rec["hash"]:
                return False, rec["seq"]
            prev = rec["hash"]
        return True, None
