"""Human-in-the-loop approval queue with segregation of duties (maker-checker)."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from sentinel.security.audit import AuditLog


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalError(Exception):
    pass


@dataclass
class ApprovalRequest:
    case_id: str
    requested_by: str
    reasons: list[str]
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: float = field(default_factory=time.time)
    decided_by: str | None = None
    decided_at: float | None = None
    comment: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ApprovalQueue:
    def __init__(self, audit: AuditLog | None = None) -> None:
        self._items: dict[str, ApprovalRequest] = {}
        self._lock = threading.Lock()
        self._audit = audit or AuditLog()

    def submit(self, case_id: str, requested_by: str, reasons: list[str], payload: dict[str, Any]) -> ApprovalRequest:
        req = ApprovalRequest(case_id, requested_by, reasons, payload)
        with self._lock:
            self._items[req.id] = req
        self._audit.append(requested_by, "approval.submitted", case_id, {"approval_id": req.id, "reasons": reasons})
        return req

    def get(self, approval_id: str) -> ApprovalRequest:
        with self._lock:
            if approval_id not in self._items:
                raise ApprovalError(f"approval {approval_id} not found")
            return self._items[approval_id]

    def for_case(self, case_id: str) -> list[ApprovalRequest]:
        with self._lock:
            return [r for r in self._items.values() if r.case_id == case_id]

    def pending(self) -> list[ApprovalRequest]:
        with self._lock:
            return [r for r in self._items.values() if r.status == ApprovalStatus.PENDING]

    def decide(self, approval_id: str, reviewer: str, approve: bool, comment: str | None = None) -> ApprovalRequest:
        with self._lock:
            req = self._items.get(approval_id)
            if req is None:
                raise ApprovalError(f"approval {approval_id} not found")
            if req.status != ApprovalStatus.PENDING:
                raise ApprovalError(f"approval {approval_id} already {req.status}")
            if reviewer == req.requested_by:
                raise ApprovalError("segregation of duties: requester cannot approve own request")
            req.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
            req.decided_by = reviewer
            req.decided_at = time.time()
            req.comment = comment
        self._audit.append(
            reviewer, f"approval.{req.status}", req.case_id, {"approval_id": approval_id, "comment": comment}
        )
        return req
