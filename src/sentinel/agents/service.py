"""Case service: the application-layer façade over the agent graph (used by API, MCP and CLI)."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from langgraph.types import Command

from sentinel.agents.workflow import build_workflow
from sentinel.container import Container
from sentinel.governance.hitl import ApprovalError
from sentinel.observability.logging import get_logger, log_event
from sentinel.observability.metrics import METRICS

log = get_logger(__name__)


class GuardrailViolation(Exception):
    def __init__(self, violations: list[str]) -> None:
        super().__init__(", ".join(violations))
        self.violations = violations


class CaseNotFound(KeyError):
    pass


class CaseService:
    def __init__(self, c: Container) -> None:
        self.c = c
        self.app = build_workflow(c)
        self._cases: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _config(self, case_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": case_id}, "recursion_limit": self.c.settings.max_agent_steps}

    def start(self, entity: str, question: str = "", requested_by: str = "system") -> dict[str, Any]:
        for text in (entity, question):
            res = self.c.guardrails.check_input(text)
            if not res.allowed:
                METRICS.inc("guardrail_blocks", stage="input")
                self.c.audit.append(requested_by, "case.blocked", entity[:80], {"violations": res.violations})
                raise GuardrailViolation(res.violations)
        case_id = f"case-{uuid.uuid4().hex[:10]}"
        with self._lock:
            self._cases[case_id] = {
                "case_id": case_id,
                "entity": entity,
                "requested_by": requested_by,
                "created_at": time.time(),
            }
        self.c.audit.append(requested_by, "case.created", case_id, {"entity": entity})
        started = time.perf_counter()
        self.app.invoke(
            {
                "case_id": case_id,
                "entity": entity,
                "question": question,
                "requested_by": requested_by,
                "iterations": 0,
                "trace": [],
                "errors": [],
                "status": "created",
            },
            self._config(case_id),
        )
        METRICS.observe("case_latency_seconds", time.perf_counter() - started)
        state = self.get(case_id)
        log_event(log, "case_run", case_id=case_id, status=state["status"])
        return state

    def get(self, case_id: str) -> dict[str, Any]:
        if case_id not in self._cases:
            raise CaseNotFound(case_id)
        snap = self.app.get_state(self._config(case_id))
        values = dict(snap.values)
        values["awaiting_human"] = bool(snap.next) and "human_review" in snap.next
        return values

    def list(self) -> list[dict[str, Any]]:
        out = []
        for cid in list(self._cases):
            st = self.get(cid)
            out.append(
                {
                    "case_id": cid,
                    "entity": st.get("entity"),
                    "status": st.get("status"),
                    "risk_band": st.get("risk", {}).get("band"),
                }
            )
        return out

    def decide(self, approval_id: str, reviewer: str, approve: bool, comment: str | None = None) -> dict[str, Any]:
        req = self.c.approvals.decide(approval_id, reviewer, approve, comment)  # enforces maker-checker
        if req.case_id not in self._cases:
            raise ApprovalError(f"case {req.case_id} not managed by this service")
        self.app.invoke(
            Command(resume={"approved": approve, "reviewer": reviewer, "comment": comment}), self._config(req.case_id)
        )
        return self.get(req.case_id)
