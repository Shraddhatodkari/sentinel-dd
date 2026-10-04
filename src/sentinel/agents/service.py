"""Case service — the application layer over the investigation graph (used by the API, MCP and CLI).

Responsibilities: input guardrails, idempotent case creation, background execution with recovery, tenant- and
ownership-scoped access control, maker-checker human decisions, and assembling the full case record
(facts → signals → assessment → policy → human decision) for the dashboard.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

from langgraph.types import Command
from sqlalchemy import func, or_, select, update

from sentinel.agents.specs import SPECS
from sentinel.agents.workflow import build_workflow
from sentinel.container import Container
from sentinel.db.models import (
    Case,
    CaseEvent,
    Evidence,
    LLMUsage,
    PolicyDecisionRecord,
    Relationship,
    Review,
    RiskAssessment,
    Signal,
)
from sentinel.observability.logging import get_logger, log_event
from sentinel.observability.metrics import METRICS
from sentinel.security.auth import Permission, Principal
from sentinel.timeutil import iso, utcnow

log = get_logger(__name__)


class GuardrailViolation(Exception):
    def __init__(self, violations: list[str]) -> None:
        super().__init__(", ".join(violations))
        self.violations = violations


class CaseNotFound(Exception):
    pass


class ReviewError(Exception):
    """Business-rule violation in the human review step (maps to 403/409)."""

    def __init__(self, message: str, code: int = 409) -> None:
        super().__init__(message)
        self.code = code


class CaseService:
    def __init__(self, c: Container) -> None:
        self.c = c
        self.app = build_workflow(c)
        self._pool = ThreadPoolExecutor(max_workers=c.settings.runner_workers, thread_name_prefix="case")
        self._inflight: set[str] = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ lifecycle
    def recover(self, stale_after_s: int = 900) -> int:
        """Mark cases orphaned by a crash/restart as failed (retryable). Called on start-up."""
        cutoff = utcnow() - timedelta(seconds=stale_after_s)
        with self.c.db.session() as s:
            rows = list(s.scalars(select(Case).where(Case.status.in_(("queued", "running")), Case.updated_at < cutoff)))
            for r in rows:
                r.status, r.error = "failed", "interrupted (process restart or timeout); retry available"
        for r in rows:
            self.c.audit.append("sentinel:runner", "case.recovered_as_failed", "case", r.id, {}, tenant=r.tenant)
        return len(rows)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _thread(self, case_id: str, attempt: int) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": f"{case_id}:{attempt}"},
            "recursion_limit": self.c.settings.max_agent_steps,
        }

    # ------------------------------------------------------------------ create / run
    def create(
        self,
        principal: Principal,
        query: str,
        *,
        cik: int | None = None,
        lei: str | None = None,
        ticker: str | None = None,
        question: str = "",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        for text in (query, question):
            res = self.c.guardrails.check_input(text)
            if not res.allowed:
                METRICS.inc("guardrail_blocks", stage="input")
                self.c.audit.append(
                    principal.subject,
                    "case.blocked",
                    "case_request",
                    query[:80],
                    {"violations": res.violations},
                    roles=principal.role_names,
                    tenant=principal.tenant,
                )
                raise GuardrailViolation(res.violations)
        if idempotency_key:
            with self.c.db.session() as s:
                existing = s.scalars(
                    select(Case).where(
                        Case.tenant == principal.tenant,
                        Case.created_by == principal.subject,
                        Case.idempotency_key == idempotency_key,
                    )
                ).first()
                if existing is not None:
                    return self.summary(existing)
        case_id = f"case-{uuid.uuid4().hex[:12]}"
        hints = {k: v for k, v in {"cik": cik, "lei": lei, "ticker": ticker}.items() if v}
        with self.c.db.session() as s:
            s.add(
                Case(
                    id=case_id,
                    tenant=principal.tenant,
                    entity_query=query.strip(),
                    identifier_hint=hints,
                    question=question,
                    status="queued",
                    created_by=principal.subject,
                    idempotency_key=idempotency_key,
                )
            )
        self.c.audit.append(
            principal.subject,
            "case.created",
            "case",
            case_id,
            {"query": query, "hints": hints},
            roles=principal.role_names,
            tenant=principal.tenant,
        )
        METRICS.inc("cases_created")
        self._submit(case_id)
        return self.get(principal, case_id)

    def _submit(self, case_id: str) -> None:
        if self.c.settings.runner_mode == "inline":
            self.run(case_id)
            return
        with self._lock:
            if case_id in self._inflight:
                return
            self._inflight.add(case_id)
        self._pool.submit(self._run_safe, case_id)

    def _run_safe(self, case_id: str) -> None:
        try:
            self.run(case_id)
        finally:
            with self._lock:
                self._inflight.discard(case_id)

    def run(self, case_id: str) -> None:
        with self.c.db.session() as s:
            case = s.get(Case, case_id)
            if case is None:
                raise CaseNotFound(case_id)
            init = {
                "case_id": case.id,
                "tenant": case.tenant,
                "query": case.entity_query,
                "hints": case.identifier_hint,
                "question": case.question,
                "requested_by": case.created_by,
                "coverage": {},
                "details": {},
                "errors": [],
                "iterations": 0,
            }
            attempt = case.attempts + 1
        try:
            self.app.invoke(init, self._thread(case_id, attempt))
        except Exception as exc:
            with self.c.db.session() as s:
                case = s.get(Case, case_id)
                if case is not None:
                    case.status, case.error = "failed", f"{type(exc).__name__}: {exc}"[:2000]
            self.c.audit.append("sentinel:runner", "case.failed", "case", case_id, {"error": str(exc)[:500]})
            METRICS.inc("cases_failed")
            log_event(log, "case_failed", case_id=case_id, error=str(exc))

    def retry(self, principal: Principal, case_id: str) -> dict[str, Any]:
        with self.c.db.session() as s:
            case = self._authorised(s, principal, case_id)
            if case.status != "failed":
                raise ReviewError(f"only failed cases can be retried (status {case.status})")
            if case.created_by != principal.subject and not principal.can(Permission.SOURCE_REFRESH):
                raise ReviewError("only the case creator or an administrator may retry a case", 403)
            for model in (Evidence, Signal, Relationship, RiskAssessment, PolicyDecisionRecord, Review):
                for row in s.scalars(select(model).where(model.case_id == case_id)):
                    s.delete(row)
            case.status, case.error = "queued", None
        self.c.audit.append(
            principal.subject, "case.retried", "case", case_id, {}, roles=principal.role_names, tenant=principal.tenant
        )
        self._submit(case_id)
        return self.get(principal, case_id)

    # ------------------------------------------------------------------ access control
    @staticmethod
    def _visible(principal: Principal) -> Any:
        cond = Case.tenant == principal.tenant
        if principal.can(Permission.CASE_READ_ALL):
            return cond
        if principal.can(Permission.CASE_READ_OWN):
            return cond & (Case.created_by == principal.subject)
        return Case.id == "__none__"

    def _authorised(self, s: Any, principal: Principal, case_id: str) -> Case:
        case = s.scalars(select(Case).where(Case.id == case_id, self._visible(principal))).first()
        if case is None:  # same error for "missing" and "forbidden": no existence oracle
            raise CaseNotFound(case_id)
        return case

    # ------------------------------------------------------------------ queries
    @staticmethod
    def summary(c: Case) -> dict[str, Any]:
        return {
            "case_id": c.id,
            "entity_query": c.entity_query,
            "resolved_name": c.resolved_name,
            "status": c.status,
            "risk_band": c.risk_band,
            "risk_score": c.risk_score,
            "policy_outcome": c.policy_outcome,
            "created_by": c.created_by,
            "created_at": iso(c.created_at),
            "updated_at": iso(c.updated_at),
            "completed_at": iso(c.completed_at),
            "error": c.error,
        }

    def list_cases(
        self,
        principal: Principal,
        status: str | None = None,
        band: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        with self.c.db.session() as s:
            cond = self._visible(principal)
            if status:
                cond = cond & (Case.status == status)
            if band:
                cond = cond & (Case.risk_band == band)
            if q:
                like = f"%{q.lower()}%"
                cond = cond & or_(func.lower(Case.entity_query).like(like), func.lower(Case.resolved_name).like(like))
            total = s.scalar(select(func.count()).select_from(Case).where(cond)) or 0
            rows = s.scalars(
                select(Case).where(cond).order_by(Case.created_at.desc()).limit(min(limit, 200)).offset(offset)
            ).all()
            return {"total": total, "items": [self.summary(r) for r in rows]}

    def get(self, principal: Principal, case_id: str) -> dict[str, Any]:
        with self.c.db.session() as s:
            case = self._authorised(s, principal, case_id)
            out = self.summary(case)
            out |= {
                "question": case.question,
                "identifier_hint": case.identifier_hint,
                "report": case.report,
                "report_grounding": case.report_grounding,
                "summary": case.summary,
                "attempts": case.attempts,
                "started_at": iso(case.started_at),
            }
            out["events"] = [
                {
                    "node": e.node,
                    "status": e.status,
                    "started_at": iso(e.started_at),
                    "duration_ms": e.duration_ms,
                    "detail": e.detail,
                    "error": e.error,
                }
                for e in s.scalars(select(CaseEvent).where(CaseEvent.case_id == case_id).order_by(CaseEvent.id))
            ]
            out["evidence"] = [
                {
                    "id": e.id,
                    "kind": e.kind,
                    "title": e.title,
                    "content": e.content[:4000],
                    "content_truncated": len(e.content) > 4000,
                    "structured": e.structured,
                    "locator": e.locator,
                    "source_id": e.source_id,
                    "source_url": e.source_url,
                    "document_id": e.document_id,
                    "retrieved_at": iso(e.retrieved_at),
                    "confidence": e.confidence,
                }
                for e in s.scalars(
                    select(Evidence).where(Evidence.case_id == case_id).order_by(Evidence.kind, Evidence.retrieved_at)
                )
            ]
            out["signals"] = [
                {
                    "id": g.id,
                    "signal_type": g.signal_type,
                    "label": g.label,
                    "value": g.value,
                    "severity": g.severity,
                    "confidence": g.confidence,
                    "evidence_ids": g.evidence_ids,
                    "derived_by": g.derived_by,
                    "detail": g.detail,
                }
                for g in s.scalars(select(Signal).where(Signal.case_id == case_id).order_by(Signal.id))
            ]
            ra = s.scalars(select(RiskAssessment).where(RiskAssessment.case_id == case_id)).first()
            out["assessment"] = (
                None
                if ra is None
                else {
                    "scorecard_version": ra.scorecard_version,
                    "score": ra.score,
                    "band": ra.band,
                    "confidence": ra.confidence,
                    "factors": ra.factors,
                    "features": ra.features,
                    "model_versions": ra.model_versions,
                    "created_at": iso(ra.created_at),
                }
            )
            pd = s.scalars(select(PolicyDecisionRecord).where(PolicyDecisionRecord.case_id == case_id)).first()
            out["policy"] = (
                None
                if pd is None
                else {
                    "policy_version": pd.policy_version,
                    "outcome": pd.outcome,
                    "required_role": pd.required_role,
                    "rules": pd.rules,
                    "created_at": iso(pd.created_at),
                }
            )
            rv = s.scalars(select(Review).where(Review.case_id == case_id)).first()
            out["review"] = None if rv is None else self._review_dict(rv)
            usage = s.execute(
                select(
                    func.count(),
                    func.sum(LLMUsage.input_tokens),
                    func.sum(LLMUsage.output_tokens),
                    func.sum(LLMUsage.cost_usd),
                ).where(LLMUsage.case_id == case_id)
            ).one()
            out["llm_usage"] = {
                "calls": usage[0] or 0,
                "input_tokens": usage[1] or 0,
                "output_tokens": usage[2] or 0,
                "cost_usd": round(usage[3] or 0.0, 6),
            }
            out["graph"] = None
            if case.summary:
                out["graph"] = case.summary.get("graph")
        snap = self.app.get_state(self._thread(case_id, max(out["attempts"], 1)))
        values = dict(snap.values or {})
        out["graph_ui"] = (values.get("graph") or {}).get("ui")
        out["graph_facts"] = (values.get("graph") or {}).get("facts")
        out["research"] = values.get("research") or (out.get("summary") or {}).get("research") or []
        out["awaiting_human"] = bool(snap.next) and "human_review" in snap.next
        return out

    @staticmethod
    def _review_dict(rv: Review) -> dict[str, Any]:
        return {
            "id": rv.id,
            "case_id": rv.case_id,
            "tier": rv.tier,
            "required_role": rv.required_role,
            "status": rv.status,
            "requested_by": rv.requested_by,
            "requested_at": iso(rv.requested_at),
            "reasons": rv.reasons,
            "decided_by": rv.decided_by,
            "decided_role": rv.decided_role,
            "decided_at": iso(rv.decided_at),
            "comment": rv.comment,
        }

    def review_queue(self, principal: Principal, status: str = "pending") -> list[dict[str, Any]]:
        if not principal.can(Permission.REVIEW_DECIDE) and not principal.can(Permission.CASE_READ_ALL):
            return []
        with self.c.db.session() as s:
            rows = s.execute(
                select(Review, Case)
                .join(Case, Case.id == Review.case_id)
                .where(Review.tenant == principal.tenant, Review.status == status)
                .order_by(Review.requested_at)
            ).all()
            return [
                self._review_dict(rv)
                | {
                    "entity": c.resolved_name or c.entity_query,
                    "risk_band": c.risk_band,
                    "risk_score": c.risk_score,
                    "can_decide": self._can_decide(principal, rv, c)[0],
                }
                for rv, c in rows
            ]

    @staticmethod
    def _can_decide(principal: Principal, rv: Review, case: Case) -> tuple[bool, str]:
        if not principal.can(Permission.REVIEW_DECIDE):
            return False, "missing permission review:decide"
        if rv.tier == "ESCALATE" and not principal.can(Permission.REVIEW_DECIDE_ESCALATED):
            return False, "escalated cases require a senior reviewer"
        if principal.subject in (rv.requested_by, case.created_by):
            return False, "segregation of duties: the case creator cannot decide its review"
        if rv.tenant != principal.tenant:
            return False, "tenant mismatch"
        return True, "ok"

    # ------------------------------------------------------------------ human decision (maker-checker)
    def decide(self, principal: Principal, case_id: str, approve: bool, comment: str) -> dict[str, Any]:
        if len((comment or "").strip()) < 10:
            raise ReviewError("a decision rationale of at least 10 characters is required", 422)
        decided_role = ""
        with self.c.db.session() as s:
            case = self._authorised(s, principal, case_id)
            rv = s.scalars(select(Review).where(Review.case_id == case_id)).first()
            if rv is None:
                raise ReviewError("this case has no review")
            ok, why = self._can_decide(principal, rv, case)
            if not ok:
                raise ReviewError(why, 403)
            decided_role = "senior_reviewer" if "senior_reviewer" in principal.role_names else "reviewer"
            res = s.execute(
                update(Review)
                .where(Review.id == rv.id, Review.status == "pending")
                .values(
                    status="approved" if approve else "rejected",
                    decided_by=principal.subject,
                    decided_role=decided_role,
                    decided_at=utcnow(),
                    comment=comment.strip(),
                )
            )
            if getattr(res, "rowcount", 0) != 1:  # optimistic concurrency: someone else already decided
                raise ReviewError("review already decided")
            case.status = "approved" if approve else "rejected"
            case.completed_at = utcnow()
            attempts = case.attempts
        self.c.audit.append(
            principal.subject,
            f"review.{'approved' if approve else 'rejected'}",
            "case",
            case_id,
            {"comment": comment.strip(), "role": decided_role},
            roles=principal.role_names,
            tenant=principal.tenant,
        )
        METRICS.inc("reviews_decided", outcome="approved" if approve else "rejected")
        try:  # resume the paused graph so the trace is complete; the decision of record is already persisted
            self.app.invoke(
                Command(resume={"approve": approve, "reviewer": principal.subject}),
                self._thread(case_id, max(attempts, 1)),
            )
        except Exception as exc:
            log_event(log, "resume_failed", case_id=case_id, error=str(exc))
        return self.get(principal, case_id)

    # ------------------------------------------------------------------ overview
    def overview(self, principal: Principal) -> dict[str, Any]:
        with self.c.db.session() as s:
            cond = self._visible(principal)
            by_status = dict(s.execute(select(Case.status, func.count()).where(cond).group_by(Case.status)).all())
            by_band = dict(
                s.execute(
                    select(Case.risk_band, func.count())
                    .where(cond, Case.risk_band.is_not(None))
                    .group_by(Case.risk_band)
                ).all()
            )
            by_outcome = dict(
                s.execute(
                    select(Case.policy_outcome, func.count())
                    .where(cond, Case.policy_outcome.is_not(None))
                    .group_by(Case.policy_outcome)
                ).all()
            )
            pending = (
                s.scalar(
                    select(func.count())
                    .select_from(Review)
                    .where(Review.tenant == principal.tenant, Review.status == "pending")
                )
                or 0
            )
            recent = [
                self.summary(c) for c in s.scalars(select(Case).where(cond).order_by(Case.created_at.desc()).limit(8))
            ]
        return {
            "total_cases": sum(by_status.values()),
            "by_status": by_status,
            "by_band": by_band,
            "by_policy_outcome": by_outcome,
            "pending_reviews": pending,
            "recent": recent,
        }

    @staticmethod
    def agent_specs() -> list[dict[str, Any]]:
        return [s.to_dict() for s in SPECS.values()]
