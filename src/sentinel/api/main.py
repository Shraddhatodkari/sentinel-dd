"""FastAPI application: REST surface for analysts, reviewers, auditors and admins."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse

from sentinel import __version__
from sentinel.agents.service import CaseNotFound, CaseService, GuardrailViolation
from sentinel.api.schemas import (
    CaseCreate,
    CaseDetail,
    CaseSummary,
    Decision,
    IngestRequest,
    SearchRequest,
    SearchResponse,
    TokenRequest,
    TokenResponse,
)
from sentinel.api.security import DevUserStore, SecurityMiddleware, TokenBucketLimiter, require
from sentinel.container import Container
from sentinel.domain import Document
from sentinel.governance.hitl import ApprovalError
from sentinel.observability.logging import configure_logging
from sentinel.observability.metrics import METRICS
from sentinel.security.auth import Permission, Principal


def create_app(container: Container | None = None, service: CaseService | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not hasattr(app.state, "container"):
            c = Container.build()
            configure_logging(c.settings.log_level)
            app.state.container = c
            app.state.service = CaseService(c)
            app.state.users = DevUserStore()
        yield

    app = FastAPI(
        title="Sentinel Due-Diligence Intelligence API",
        version=__version__,
        lifespan=lifespan,
        description="Governed multi-agent due diligence: GraphRAG + ML risk + HITL approvals.",
    )
    if container is not None:
        app.state.container = container
        app.state.service = service or CaseService(container)
        app.state.users = DevUserStore()
    rate = container.settings.rate_limit_per_minute if container else 120
    app.add_middleware(SecurityMiddleware, limiter=TokenBucketLimiter(rate))

    def svc(request: Request) -> CaseService:
        return request.app.state.service

    def ctr(request: Request) -> Container:
        return request.app.state.container

    # ------------------------------------------------------------------ ops
    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/ready", tags=["ops"])
    def ready(c: Container = Depends(ctr)) -> JSONResponse:
        ok = c.store.count() > 0
        return JSONResponse(
            {"ready": ok, "chunks": c.store.count(), "graph": c.graph.stats(), "model": c.risk.model_version},
            status_code=200 if ok else 503,
        )

    @app.get("/metrics", tags=["ops"], response_class=PlainTextResponse)
    def metrics() -> str:
        return METRICS.render_prometheus()

    # ------------------------------------------------------------------ auth
    @app.post("/auth/token", response_model=TokenResponse, tags=["auth"])
    def token(body: TokenRequest, request: Request, c: Container = Depends(ctr)) -> TokenResponse:
        if c.settings.env == "prod":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "use the corporate identity provider")
        roles = request.app.state.users.authenticate(body.username, body.password)
        if roles is None:
            c.audit.append(body.username, "auth.failed", "auth")
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
        c.audit.append(body.username, "auth.login", "auth")
        return TokenResponse(access_token=c.tokens.issue(body.username, roles), roles=[r.value for r in roles])

    # ------------------------------------------------------------------ cases
    @app.post("/cases", response_model=CaseDetail, status_code=201, tags=["cases"])
    def create_case(
        body: CaseCreate, p: Principal = Depends(require(Permission.CASE_CREATE)), s: CaseService = Depends(svc)
    ) -> dict[str, Any]:
        try:
            return s.start(body.entity, body.question, requested_by=p.subject)
        except GuardrailViolation as exc:
            raise HTTPException(422, {"error": "guardrail_violation", "violations": exc.violations}) from exc

    @app.get("/cases", response_model=list[CaseSummary], tags=["cases"])
    def list_cases(
        _: Principal = Depends(require(Permission.CASE_READ)), s: CaseService = Depends(svc)
    ) -> list[dict[str, Any]]:
        return s.list()

    @app.get("/cases/{case_id}", response_model=CaseDetail, tags=["cases"])
    def get_case(
        case_id: str, _: Principal = Depends(require(Permission.CASE_READ)), s: CaseService = Depends(svc)
    ) -> dict[str, Any]:
        try:
            return s.get(case_id)
        except CaseNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "case not found") from exc

    # ------------------------------------------------------------------ approvals (HITL)
    @app.get("/approvals", tags=["governance"])
    def pending(
        _: Principal = Depends(require(Permission.APPROVAL_DECIDE)), c: Container = Depends(ctr)
    ) -> list[dict[str, Any]]:
        return [r.to_dict() for r in c.approvals.pending()]

    @app.post("/approvals/{approval_id}/decision", response_model=CaseDetail, tags=["governance"])
    def decide(
        approval_id: str,
        body: Decision,
        p: Principal = Depends(require(Permission.APPROVAL_DECIDE)),
        s: CaseService = Depends(svc),
    ) -> dict[str, Any]:
        try:
            return s.decide(approval_id, p.subject, body.approve, body.comment)
        except ApprovalError as exc:
            code = status.HTTP_404_NOT_FOUND if "not found" in str(exc) else status.HTTP_409_CONFLICT
            raise HTTPException(code, str(exc)) from exc

    # ------------------------------------------------------------------ retrieval
    @app.post("/search", response_model=SearchResponse, tags=["retrieval"])
    def search(
        body: SearchRequest, p: Principal = Depends(require(Permission.SEARCH)), c: Container = Depends(ctr)
    ) -> SearchResponse:
        chk = c.guardrails.check_input(body.query)
        if not chk.allowed:
            c.audit.append(p.subject, "search.blocked", "search", {"violations": chk.violations})
            raise HTTPException(422, {"error": "guardrail_violation", "violations": chk.violations})
        ev = c.qa.answer(chk.sanitized or body.query, k=body.k, flt={"entity": body.entity} if body.entity else None)
        c.audit.append(p.subject, "search", "search", {"query": chk.sanitized, "citations": ev.citations})
        return SearchResponse(
            answer=ev.answer,
            citations=ev.citations,
            grounding_score=ev.grounding_score,
            flagged_injection=ev.flagged_injection,
        )

    @app.get("/graph/{entity}", tags=["retrieval"])
    def graph(
        entity: str, hops: int = 2, _: Principal = Depends(require(Permission.SEARCH)), c: Container = Depends(ctr)
    ) -> dict[str, Any]:
        return c.graph.subgraph_json(entity, min(max(hops, 1), 4))

    # ------------------------------------------------------------------ admin
    @app.post("/documents", status_code=201, tags=["admin"])
    def ingest(
        body: IngestRequest, p: Principal = Depends(require(Permission.DOC_INGEST)), c: Container = Depends(ctr)
    ) -> dict[str, Any]:
        rep = c.ingestion.ingest([Document(body.id, body.title, body.text, body.metadata)])
        c.retriever.known_entities = sorted(c.ingestion.entities | set(c.entities.names()))
        c.audit.append(p.subject, "document.ingested", body.id, {"chunks": rep.chunks, "pii": rep.pii_findings})
        return rep.__dict__

    @app.get("/audit", tags=["governance"])
    def audit(
        resource: str | None = None, _: Principal = Depends(require(Permission.AUDIT_READ)), c: Container = Depends(ctr)
    ) -> list[dict[str, Any]]:
        return c.audit.records(resource)[-500:]

    @app.get("/audit/verify", tags=["governance"])
    def audit_verify(
        _: Principal = Depends(require(Permission.AUDIT_READ)), c: Container = Depends(ctr)
    ) -> dict[str, Any]:
        ok, bad = c.audit.verify()
        return {"intact": ok, "first_bad_seq": bad, "records": len(c.audit.records())}

    return app


def run() -> None:  # pragma: no cover
    import uvicorn

    uvicorn.run("sentinel.api.main:create_app", factory=True, host="0.0.0.0", port=8000)  # noqa: S104  # nosec B104
