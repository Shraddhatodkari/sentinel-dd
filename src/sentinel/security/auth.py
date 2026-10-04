"""JWT authentication + role-based access control (RBAC) with least-privilege permissions."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from enum import StrEnum

import jwt


class Role(StrEnum):
    ANALYST = "analyst"  # run investigations, read reports
    REVIEWER = "reviewer"  # approve/reject HITL decisions
    AUDITOR = "auditor"  # read-only audit trail access
    ADMIN = "admin"  # ingest, train models, manage system


class Permission(StrEnum):
    CASE_CREATE = "case:create"
    CASE_READ = "case:read"
    DOC_INGEST = "doc:ingest"
    SEARCH = "search"
    APPROVAL_DECIDE = "approval:decide"
    AUDIT_READ = "audit:read"
    MODEL_TRAIN = "model:train"
    PII_UNMASK = "pii:unmask"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ANALYST: frozenset({Permission.CASE_CREATE, Permission.CASE_READ, Permission.SEARCH}),
    Role.REVIEWER: frozenset({Permission.CASE_READ, Permission.SEARCH, Permission.APPROVAL_DECIDE}),
    Role.AUDITOR: frozenset({Permission.CASE_READ, Permission.AUDIT_READ}),
    Role.ADMIN: frozenset(Permission),
}


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: frozenset[Role]
    tenant: str = "default"

    @property
    def permissions(self) -> frozenset[Permission]:
        perms: set[Permission] = set()
        for r in self.roles:
            perms |= ROLE_PERMISSIONS[r]
        return frozenset(perms)

    def can(self, perm: Permission) -> bool:
        return perm in self.permissions


class TokenService:
    def __init__(self, secret: str, algorithm: str = "HS256", ttl_minutes: int = 60, issuer: str = "sentinel-dd"):
        if len(secret) < 32:
            raise ValueError("JWT secret must be at least 32 characters")
        self._secret = secret
        self._alg = algorithm
        self._ttl = ttl_minutes * 60
        self._issuer = issuer
        self._revoked: set[str] = set()

    def issue(self, subject: str, roles: list[Role] | list[str], tenant: str = "default") -> str:
        now = int(time.time())
        payload = {
            "sub": subject,
            "roles": [Role(r).value for r in roles],
            "tenant": tenant,
            "iat": now,
            "nbf": now,
            "exp": now + self._ttl,
            "iss": self._issuer,
            "jti": uuid.uuid4().hex,
        }
        return jwt.encode(payload, self._secret, algorithm=self._alg)

    def revoke(self, token: str) -> None:
        claims = jwt.decode(token, self._secret, algorithms=[self._alg], options={"verify_exp": False})
        self._revoked.add(claims["jti"])

    def verify(self, token: str) -> Principal:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[self._alg],
                issuer=self._issuer,
                options={"require": ["exp", "sub", "iat", "jti"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("token expired") from exc
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid token: {exc}") from exc
        if claims["jti"] in self._revoked:
            raise AuthError("token revoked")
        try:
            roles = frozenset(Role(r) for r in claims.get("roles", []))
        except ValueError as exc:
            raise AuthError("unknown role in token") from exc
        return Principal(claims["sub"], roles, claims.get("tenant", "default"))
