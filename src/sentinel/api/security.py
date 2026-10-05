"""API security: bearer auth dependency, permission guards, rate limiting, request-size limits, security headers
(including a strict CSP for the dashboard), request-id correlation and safe error responses."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from sentinel.observability.logging import get_logger, log_event, request_id_ctx
from sentinel.observability.metrics import METRICS
from sentinel.security.auth import AuthError, Permission, Principal

bearer = HTTPBearer(auto_error=False)
log = get_logger(__name__)

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)

# Swagger UI (/api/docs only) loads its bundle from jsDelivr and boots with an inline script.
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)


def current_principal(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "missing bearer token", headers={"WWW-Authenticate": "Bearer"}
        )
    try:
        principal, jti, exp = request.app.state.container.tokens.verify(creds.credentials)
    except AuthError as exc:
        METRICS.inc("auth_failures", reason=str(exc))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc
    # account may have been disabled after the token was issued
    if request.app.state.users.get_principal(principal.subject) is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account disabled", headers={"WWW-Authenticate": "Bearer"})
    request.state.principal, request.state.jti, request.state.exp = principal, jti, exp
    return principal


def require(*permissions: Permission) -> Callable[[Principal], Principal]:
    """Grant if the principal holds ANY of ``permissions``."""

    def guard(principal: Principal = Depends(current_principal)) -> Principal:
        if not any(principal.can(p) for p in permissions):
            METRICS.inc("authz_denied", permission=permissions[0].value)
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing permission {permissions[0].value}")
        return principal

    return guard


class TokenBucketLimiter:
    def __init__(self, rate_per_minute: int) -> None:
        self.capacity = float(max(rate_per_minute, 1))
        self.refill = self.capacity / 60.0
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if len(self._buckets) > 50_000:  # bound memory under key-spraying
                self._buckets.clear()
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.refill)
            if tokens < 1:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1, now)
            return True


EXEMPT = ("/health", "/ready", "/metrics")


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, limiter: TokenBucketLimiter, max_body_bytes: int = 1_000_000) -> None:
        super().__init__(app)
        self.limiter = limiter
        self.max_body = max_body_bytes

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        rid = request.headers.get("x-request-id", "")
        rid = rid if rid and len(rid) <= 64 and rid.replace("-", "").isalnum() else uuid.uuid4().hex
        token = request_id_ctx.set(rid)
        path = request.url.path
        start = time.perf_counter()
        try:
            client = request.client.host if request.client else "anon"
            key = (request.headers.get("authorization", "")[-32:] or client) + ":" + client
            if int(request.headers.get("content-length") or 0) > self.max_body:
                resp: Response = JSONResponse({"error": "payload_too_large", "request_id": rid}, status_code=413)
            elif path.startswith("/api/") and not self.limiter.allow(key):
                METRICS.inc("rate_limited")
                resp = JSONResponse(
                    {"error": "rate_limited", "request_id": rid}, status_code=429, headers={"Retry-After": "10"}
                )
            else:
                resp = await call_next(request)
        except Exception as exc:  # never leak stack traces to clients
            log_event(log, "unhandled_exception", path=path, error=f"{type(exc).__name__}: {exc}")
            resp = JSONResponse({"error": "internal_error", "request_id": rid}, status_code=500)
        finally:
            request_id_ctx.reset(token)
        route = path if path in EXEMPT or not path.startswith("/api/") else "/api"
        METRICS.inc("http_requests", status=str(resp.status_code), route=route)
        METRICS.observe("http_request_seconds", time.perf_counter() - start, route=route)
        resp.headers["x-request-id"] = rid
        resp.headers["x-content-type-options"] = "nosniff"
        resp.headers["x-frame-options"] = "DENY"
        resp.headers["referrer-policy"] = "no-referrer"
        resp.headers["permissions-policy"] = "camera=(), microphone=(), geolocation=()"
        resp.headers["strict-transport-security"] = "max-age=31536000; includeSubDomains"
        resp.headers["cross-origin-opener-policy"] = "same-origin"
        resp.headers["content-security-policy"] = DOCS_CSP if path == "/api/docs" else CSP
        if path.startswith("/api/"):
            resp.headers["cache-control"] = "no-store"
        return resp
