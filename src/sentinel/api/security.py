"""API security: bearer-token auth dependency, permission guard, rate limiting, security headers,
request-id correlation, and dev-only identity store (replace with OIDC/SAML IdP in production)."""

from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from sentinel.observability.logging import request_id_ctx
from sentinel.observability.metrics import METRICS
from sentinel.security.auth import AuthError, Permission, Principal, Role

bearer = HTTPBearer(auto_error=False)


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)


class DevUserStore:
    """PBKDF2-hashed demo users. Disabled when SENTINEL_ENV=prod."""

    def __init__(self) -> None:
        self._users: dict[str, tuple[bytes, bytes, list[Role]]] = {}
        default_pw = os.environ.get("SENTINEL_DEMO_PASSWORD", "sentinel-demo")
        for name, roles in {
            "alice": [Role.ANALYST],
            "bob": [Role.REVIEWER],
            "carol": [Role.AUDITOR],
            "admin": [Role.ADMIN],
        }.items():
            salt = os.urandom(16)
            self._users[name] = (salt, _hash(default_pw, salt), roles)

    def authenticate(self, username: str, password: str) -> list[Role] | None:
        rec = self._users.get(username)
        if not rec:
            _hash(password, b"timing-equaliser")  # constant-ish time for unknown users
            return None
        salt, digest, roles = rec
        return roles if hmac.compare_digest(_hash(password, salt), digest) else None


def current_principal(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "missing bearer token", headers={"WWW-Authenticate": "Bearer"}
        )
    try:
        principal: Principal = request.app.state.container.tokens.verify(creds.credentials)
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc
    request.state.principal = principal
    return principal


def require(permission: Permission) -> Callable[[Principal], Principal]:
    def guard(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.can(permission):
            METRICS.inc("authz_denied", permission=permission.value)
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing permission {permission.value}")
        return principal

    return guard


class TokenBucketLimiter:
    def __init__(self, rate_per_minute: int) -> None:
        self.capacity = float(rate_per_minute)
        self.refill = rate_per_minute / 60.0
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.refill)
            if tokens < 1:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1, now)
            return True


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, limiter: TokenBucketLimiter) -> None:
        super().__init__(app)
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_ctx.set(rid)
        try:
            client_key = request.headers.get("authorization", "")[-24:] or (
                request.client.host if request.client else "anon"
            )
            if request.url.path not in ("/health", "/ready", "/metrics") and not self.limiter.allow(client_key):
                METRICS.inc("rate_limited")
                resp: Response = JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
            else:
                start = time.perf_counter()
                resp = await call_next(request)
                METRICS.observe("http_request_seconds", time.perf_counter() - start, path=request.url.path)
            METRICS.inc("http_requests", status=str(resp.status_code))
            resp.headers["x-request-id"] = rid
            resp.headers["x-content-type-options"] = "nosniff"
            resp.headers["x-frame-options"] = "DENY"
            resp.headers["referrer-policy"] = "no-referrer"
            resp.headers["cache-control"] = "no-store"
            resp.headers["strict-transport-security"] = "max-age=31536000; includeSubDomains"
            return resp
        finally:
            request_id_ctx.reset(token)
