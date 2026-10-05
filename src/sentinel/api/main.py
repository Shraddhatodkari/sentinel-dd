"""FastAPI application: versioned REST API (``/api/v1``) + the analyst dashboard (static SPA at ``/``)."""

from __future__ import annotations

import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from sentinel import __version__
from sentinel.agents.service import CaseNotFound, CaseService, GuardrailViolation, ReviewError
from sentinel.api.schemas import CaseCreate, CaseList, Decision, LoginRequest, ScreenRequest, TokenResponse, UserCreate
from sentinel.api.security import SecurityMiddleware, TokenBucketLimiter, current_principal, require
from sentinel.container import Container
from sentinel.db.migrate import current_revision, head_revision
from sentinel.db.models import IngestionRun, LLMUsage, SourceDocument
from sentinel.ingestion.reference import freshness
from sentinel.observability.logging import configure_logging, request_id_ctx
from sentinel.observability.metrics import METRICS
from sentinel.risk.jurisdictions import load_policy
from sentinel.security.auth import Permission, Principal
from sentinel.security.users import UserError, UserService
from sentinel.sources.catalog import SOURCES
from sentinel.timeutil import iso

# Built SPA location: repo checkout by default; containers set SENTINEL_DASHBOARD_DIR.
DASHBOARD_DIR = Path(
    os.environ.get("SENTINEL_DASHBOARD_DIR") or Path(__file__).resolve().parents[3] / "dashboard" / "dist"
)


def _err(code: int, error: str, detail: Any = None) -> JSONResponse:
    return JSONResponse({"error": error, "detail": detail, "request_id": request_id_ctx.get()}, status_code=code)


def create_app(
    container: Container | None = None, service: CaseService | None = None, dashboard_dir: Path | None = None
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not hasattr(app.state, "container"):
            c = Container.build()
            configure_logging(c.settings.log_level)
            _attach(app, c, None)
        app.state.service.recover()
        yield
        app.state.service.shutdown()

    app = FastAPI(
        title="Sentinel — Due-Diligence & Third-Party Risk Intelligence API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    if container is not None:
        _attach(app, container, service)
    rate = container.settings.rate_limit_per_minute if container else 120
    app.add_middleware(SecurityMiddleware, limiter=TokenBucketLimiter(rate))
    if container is not None and container.settings.cors_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=container.settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        )
    login_limiter = TokenBucketLimiter(container.settings.login_rate_limit_per_minute if container else 10)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_r: Request, exc: RequestValidationError) -> JSONResponse:
        return _err(422, "validation_error", [{"loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()])

    @app.exception_handler(HTTPException)
    async def http_handler(_r: Request, exc: HTTPException) -> JSONResponse:
        resp = _err(
            exc.status_code,
            {
                401: "unauthorized",
                403: "forbidden",
                404: "not_found",
                409: "conflict",
                422: "unprocessable",
                429: "rate_limited",
            }.get(exc.status_code, "error"),
            exc.detail,
        )
        for k, v in (exc.headers or {}).items():
            resp.headers[k] = v
        return resp

    def ctr(request: Request) -> Container:
        return request.app.state.container

    def svc(request: Request) -> CaseService:
        return request.app.state.service

    # ------------------------------------------------------------------ ops
    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/ready", tags=["ops"])
    def ready(c: Container = Depends(ctr)) -> JSONResponse:
        db_ok = c.db.ping()
        rev, head = (current_revision(c.db.engine), head_revision(c.db.engine)) if db_ok else (None, None)
        c.screening.ensure_loaded() if db_ok else None
        checks = {
            "database": db_ok,
            "migrations_at_head": rev == head and rev is not None,
            "screening_lists_loaded": c.screening.size > 0,
        }
        ok = all(checks.values())
        return JSONResponse(
            {"ready": ok, "checks": checks, "schema_revision": rev, "screening_records": c.screening.size},
            status_code=200 if ok else 503,
        )

    @app.get("/metrics", tags=["ops"], response_class=PlainTextResponse)
    def metrics() -> str:
        return METRICS.render_prometheus()

    # ------------------------------------------------------------------ auth
    @app.post("/api/v1/auth/login", response_model=TokenResponse, tags=["auth"])
    def login(body: LoginRequest, request: Request, c: Container = Depends(ctr)) -> TokenResponse:
        client = request.client.host if request.client else "anon"
        if not c.settings.local_auth_enabled:
            raise HTTPException(404, "local authentication is disabled; use the corporate identity provider")
        if not login_limiter.allow(f"{client}:{body.username}"):
            raise HTTPException(429, "too many login attempts")
        users: UserService = request.app.state.users
        principal = users.authenticate(body.username, body.password)
        if principal is None:
            c.audit.append(
                body.username[:64],
                "auth.login_failed",
                "user",
                body.username[:64],
                {"locked": users.is_locked(body.username)},
            )
            raise HTTPException(401, "invalid credentials")
        token, exp = c.tokens.issue(principal.subject, list(principal.roles), principal.tenant, principal.display_name)
        c.audit.append(
            principal.subject,
            "auth.login",
            "user",
            principal.subject,
            {},
            roles=principal.role_names,
            tenant=principal.tenant,
        )
        return TokenResponse(
            access_token=token,
            expires_at=exp,
            username=principal.subject,
            display_name=principal.display_name,
            roles=principal.role_names,
            permissions=sorted(p.value for p in principal.permissions),
            tenant=principal.tenant,
        )

    @app.post("/api/v1/auth/logout", status_code=204, tags=["auth"])
    def logout(request: Request, p: Principal = Depends(current_principal), c: Container = Depends(ctr)) -> Response:
        c.revoke_token(request.state.jti, p.subject, request.state.exp)
        c.audit.append(p.subject, "auth.logout", "user", p.subject, {}, roles=p.role_names, tenant=p.tenant)
        return Response(status_code=204)

    @app.get("/api/v1/auth/me", tags=["auth"])
    def me(p: Principal = Depends(current_principal)) -> dict[str, Any]:
        return {
            "username": p.subject,
            "display_name": p.display_name,
            "roles": p.role_names,
            "tenant": p.tenant,
            "permissions": sorted(x.value for x in p.permissions),
        }

    # ------------------------------------------------------------------ cases
    @app.post("/api/v1/cases", status_code=202, tags=["cases"])
    def create_case(
        body: CaseCreate,
        p: Principal = Depends(require(Permission.CASE_CREATE)),
        s: CaseService = Depends(svc),
        idempotency_key: str | None = Header(None, max_length=128),
    ) -> dict[str, Any]:
        try:
            return s.create(
                p,
                body.query,
                cik=body.cik,
                lei=body.lei,
                ticker=body.ticker,
                question=body.question,
                idempotency_key=idempotency_key,
            )
        except GuardrailViolation as exc:
            raise HTTPException(422, {"error": "guardrail_violation", "violations": exc.violations}) from exc

    @app.get("/api/v1/cases", response_model=CaseList, tags=["cases"])
    def list_cases(
        p: Principal = Depends(require(Permission.CASE_READ_OWN, Permission.CASE_READ_ALL)),
        s: CaseService = Depends(svc),
        status_: str | None = Query(None, alias="status", max_length=24),
        band: str | None = Query(None, pattern="^(LOW|MEDIUM|HIGH)$"),
        q: str | None = Query(None, max_length=100),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        return s.list_cases(p, status_, band, q, limit, offset)

    @app.get("/api/v1/cases/{case_id}", tags=["cases"])
    def get_case(
        case_id: str,
        p: Principal = Depends(require(Permission.CASE_READ_OWN, Permission.CASE_READ_ALL)),
        s: CaseService = Depends(svc),
    ) -> dict[str, Any]:
        try:
            return s.get(p, case_id)
        except CaseNotFound as exc:
            raise HTTPException(404, "case not found") from exc

    @app.post("/api/v1/cases/{case_id}/retry", status_code=202, tags=["cases"])
    def retry_case(
        case_id: str,
        p: Principal = Depends(require(Permission.CASE_CREATE, Permission.SOURCE_REFRESH)),
        s: CaseService = Depends(svc),
    ) -> dict[str, Any]:
        try:
            return s.retry(p, case_id)
        except CaseNotFound as exc:
            raise HTTPException(404, "case not found") from exc
        except ReviewError as exc:
            raise HTTPException(exc.code, str(exc)) from exc

    @app.post("/api/v1/cases/{case_id}/decision", tags=["reviews"])
    def decide(
        case_id: str,
        body: Decision,
        p: Principal = Depends(require(Permission.REVIEW_DECIDE)),
        s: CaseService = Depends(svc),
    ) -> dict[str, Any]:
        try:
            return s.decide(p, case_id, body.approve, body.comment)
        except CaseNotFound as exc:
            raise HTTPException(404, "case not found") from exc
        except ReviewError as exc:
            raise HTTPException(exc.code, str(exc)) from exc

    @app.get("/api/v1/reviews", tags=["reviews"])
    def reviews(
        p: Principal = Depends(require(Permission.REVIEW_DECIDE, Permission.CASE_READ_ALL)),
        s: CaseService = Depends(svc),
        status_: str = Query("pending", alias="status", pattern="^(pending|approved|rejected)$"),
    ) -> list[dict[str, Any]]:
        return s.review_queue(p, status_)

    @app.get("/api/v1/overview", tags=["cases"])
    def overview(
        p: Principal = Depends(current_principal), s: CaseService = Depends(svc), c: Container = Depends(ctr)
    ) -> dict[str, Any]:
        fresh = freshness(c.db)
        return s.overview(p) | {
            "sources": [
                {
                    k: f[k]
                    for k in (
                        "source_id",
                        "name",
                        "kind",
                        "stale",
                        "age_hours",
                        "current_records",
                        "enabled",
                        "freshness_sla_h",
                    )
                }
                | {"last_status": (f["last_run"] or {}).get("status")}
                for f in fresh
            ]
        }

    # ------------------------------------------------------------------ screening (ad hoc)
    @app.post("/api/v1/screening", tags=["screening"])
    def screen(
        body: ScreenRequest, p: Principal = Depends(require(Permission.SEARCH)), c: Container = Depends(ctr)
    ) -> dict[str, Any]:
        hits = c.screening.screen(body.name, "adhoc", body.entity_type, c.settings.screening_review_threshold)
        c.audit.append(
            p.subject,
            "screening.adhoc",
            "name",
            body.name[:120],
            {"hits": len(hits)},
            roles=p.role_names,
            tenant=p.tenant,
        )
        return {
            "query": body.name,
            "threshold": c.settings.screening_review_threshold,
            "lists": c.screening.counts(),
            "hits": [h.to_dict() for h in hits[:25]],
        }

    # ------------------------------------------------------------------ sources & lineage
    @app.get("/api/v1/sources", tags=["sources"])
    def sources(_: Principal = Depends(current_principal), c: Container = Depends(ctr)) -> list[dict[str, Any]]:
        return freshness(c.db)

    @app.post("/api/v1/sources/{source_id}/refresh", status_code=202, tags=["sources"])
    def refresh(
        source_id: str, p: Principal = Depends(require(Permission.SOURCE_REFRESH)), c: Container = Depends(ctr)
    ) -> dict[str, Any]:
        if source_id not in SOURCES or SOURCES[source_id].kind not in ("sanctions", "pep"):
            raise HTTPException(404, "unknown reference source")
        c.audit.append(
            p.subject, "source.refresh_requested", "source", source_id, {}, roles=p.role_names, tenant=p.tenant
        )
        threading.Thread(target=c.reference.refresh, args=(source_id, p.subject), daemon=True).start()
        return {"source_id": source_id, "status": "started"}

    @app.get("/api/v1/sources/runs", tags=["sources"])
    def runs(
        _: Principal = Depends(require(Permission.SYSTEM_READ, Permission.SOURCE_REFRESH)),
        c: Container = Depends(ctr),
        limit: int = Query(50, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        with c.db.session() as sess:
            rows = sess.scalars(select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(limit)).all()
            return [
                {
                    "id": r.id,
                    "source_id": r.source_id,
                    "kind": r.kind,
                    "status": r.status,
                    "started_at": iso(r.started_at),
                    "finished_at": iso(r.finished_at),
                    "records_loaded": r.records_loaded,
                    "records_rejected": r.records_rejected,
                    "unchanged": r.unchanged,
                    "quality": r.quality,
                    "error": r.error,
                    "document_id": r.document_id,
                    "triggered_by": r.triggered_by,
                }
                for r in rows
            ]

    @app.get("/api/v1/documents/{document_id}", tags=["sources"])
    def document(
        document_id: str,
        p: Principal = Depends(require(Permission.CASE_READ_ALL, Permission.AUDIT_READ, Permission.CASE_READ_OWN)),
        c: Container = Depends(ctr),
    ) -> dict[str, Any]:
        with c.db.session() as sess:
            d = sess.get(SourceDocument, document_id)
            if d is None:
                raise HTTPException(404, "document not found")
            return {
                "id": d.id,
                "source_id": d.source_id,
                "url": d.url,
                "retrieved_at": iso(d.retrieved_at),
                "sha256": d.sha256,
                "content_type": d.content_type,
                "size_bytes": d.size_bytes,
                "http_status": d.http_status,
                "integrity_verified": c.fetcher.raw.exists(d.sha256),
            }

    # ------------------------------------------------------------------ audit & governance
    @app.get("/api/v1/audit", tags=["governance"])
    def audit(
        p: Principal = Depends(require(Permission.AUDIT_READ)),
        c: Container = Depends(ctr),
        resource_id: str | None = Query(None, max_length=128),
        limit: int = Query(200, ge=1, le=1000),
        before: int | None = Query(None, ge=0),
    ) -> list[dict[str, Any]]:
        return c.audit.records(resource_id=resource_id, tenant=p.tenant, limit=limit, before_seq=before)

    @app.get("/api/v1/audit/verify", tags=["governance"])
    def audit_verify(
        p: Principal = Depends(require(Permission.AUDIT_READ)), c: Container = Depends(ctr)
    ) -> dict[str, Any]:
        res = c.audit.verify()
        c.audit.append(
            p.subject,
            "audit.verified",
            "audit",
            "chain",
            {"intact": res["intact"]},
            roles=p.role_names,
            tenant=p.tenant,
        )
        return res

    @app.get("/api/v1/governance", tags=["governance"])
    def governance(
        _: Principal = Depends(current_principal), c: Container = Depends(ctr), s: CaseService = Depends(svc)
    ) -> dict[str, Any]:
        return {
            "policy_version": c.settings.policy_version,
            "scorecard_version": c.settings.scorecard_version,
            "auto_clear_enabled": c.settings.auto_clear_enabled,
            "screening_threshold": c.settings.screening_review_threshold,
            "grounding_threshold": c.settings.grounding_threshold,
            "jurisdiction_policy": load_policy(),
            "llm": {"provider": c.llm.provider, "model": c.llm.model},
            "peer_model": c.peer_model_version,
            "agents": s.agent_specs(),
            "sources": [
                {"id": d.id, "name": d.name, "authority": d.authority, "license": d.license, "kind": d.kind}
                for d in SOURCES.values()
            ],
        }

    @app.get("/api/v1/system/status", tags=["ops"])
    def system_status(
        _: Principal = Depends(require(Permission.SYSTEM_READ)), c: Container = Depends(ctr)
    ) -> dict[str, Any]:
        with c.db.session() as sess:
            usage = sess.execute(
                select(
                    LLMUsage.provider,
                    func.count(),
                    func.sum(LLMUsage.input_tokens),
                    func.sum(LLMUsage.output_tokens),
                    func.sum(LLMUsage.cost_usd),
                ).group_by(LLMUsage.provider)
            ).all()
        return {
            "version": __version__,
            "database": c.db.ping(),
            "schema_revision": current_revision(c.db.engine),
            "screening_records": c.screening.counts(),
            "circuit_breakers": c.http.breaker_states(),
            "llm": {"provider": c.llm.provider, "model": c.llm.model},
            "llm_usage": [
                {
                    "provider": u[0],
                    "calls": u[1],
                    "input_tokens": u[2] or 0,
                    "output_tokens": u[3] or 0,
                    "cost_usd": round(u[4] or 0, 6),
                }
                for u in usage
            ],
            "peer_model": c.peer_model_version,
            "runner_mode": c.settings.runner_mode,
            "audit_records": c.audit.count(),
        }

    # ------------------------------------------------------------------ users
    @app.get("/api/v1/users", tags=["admin"])
    def users_list(request: Request, p: Principal = Depends(require(Permission.USER_MANAGE))) -> list[dict[str, Any]]:
        return request.app.state.users.list(p.tenant)

    @app.post("/api/v1/users", status_code=201, tags=["admin"])
    def users_create(
        body: UserCreate,
        request: Request,
        p: Principal = Depends(require(Permission.USER_MANAGE)),
        c: Container = Depends(ctr),
    ) -> dict[str, Any]:
        try:
            u = request.app.state.users.create(
                body.username, body.password, list(body.roles), p.tenant, body.display_name
            )
        except UserError as exc:
            raise HTTPException(422, str(exc)) from exc
        c.audit.append(
            p.subject,
            "user.created",
            "user",
            body.username,
            {"roles": list(body.roles)},
            roles=p.role_names,
            tenant=p.tenant,
        )
        return u

    # ------------------------------------------------------------------ dashboard (SPA)
    dist = dashboard_dir or DASHBOARD_DIR
    if (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> Response:
            if full_path.startswith(("api/", "health", "ready", "metrics")):
                raise HTTPException(404, "not found")
            candidate = (dist / full_path).resolve()
            if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html", headers={"cache-control": "no-cache"})
    else:

        @app.get("/", include_in_schema=False)
        def root() -> JSONResponse:
            return JSONResponse(
                {
                    "service": "sentinel-dd",
                    "api_docs": "/api/docs",
                    "dashboard": "not built — run `npm --prefix dashboard ci && npm --prefix dashboard run build`",
                },
                status_code=status.HTTP_200_OK,
            )

    return app


def _attach(app: FastAPI, c: Container, service: CaseService | None) -> None:
    app.state.container = c
    app.state.service = service or CaseService(c)
    app.state.users = UserService(c.db, c.settings.password_min_length)


def run() -> None:  # pragma: no cover
    import uvicorn

    uvicorn.run("sentinel.api.main:create_app", factory=True, host="0.0.0.0", port=8000)  # noqa: S104  # nosec B104
