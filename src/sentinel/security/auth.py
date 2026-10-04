"""JWT authentication and role-based access control (least privilege).

Roles are deliberately separated so no single role can both create and approve a case (maker-checker), and
platform administration does not imply business decision rights.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from enum import StrEnum

import jwt


class Role(StrEnum):
    ANALYST = "analyst"  # opens investigations, sees own cases
    REVIEWER = "reviewer"  # decides REVIEW-tier cases
    SENIOR_REVIEWER = "senior_reviewer"  # decides REVIEW and ESCALATE-tier cases (sanctions / high risk)
    AUDITOR = "auditor"  # read-only access to cases, audit trail and lineage
    ADMIN = "admin"  # platform administration: users, source refresh, model training (no business decisions)


class Permission(StrEnum):
    CASE_CREATE = "case:create"
    CASE_READ_OWN = "case:read_own"
    CASE_READ_ALL = "case:read_all"
    SEARCH = "search"
    REVIEW_DECIDE = "review:decide"
    REVIEW_DECIDE_ESCALATED = "review:decide_escalated"
    AUDIT_READ = "audit:read"
    SOURCE_REFRESH = "source:refresh"
    MODEL_TRAIN = "model:train"
    USER_MANAGE = "user:manage"
    SYSTEM_READ = "system:read"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ANALYST: frozenset({Permission.CASE_CREATE, Permission.CASE_READ_OWN, Permission.SEARCH}),
    Role.REVIEWER: frozenset({Permission.CASE_READ_ALL, Permission.SEARCH, Permission.REVIEW_DECIDE}),
    Role.SENIOR_REVIEWER: frozenset(
        {Permission.CASE_READ_ALL, Permission.SEARCH, Permission.REVIEW_DECIDE, Permission.REVIEW_DECIDE_ESCALATED}
    ),
    Role.AUDITOR: frozenset({Permission.CASE_READ_ALL, Permission.AUDIT_READ, Permission.SYSTEM_READ}),
    Role.ADMIN: frozenset(
        {
            Permission.USER_MANAGE,
            Permission.SOURCE_REFRESH,
            Permission.MODEL_TRAIN,
            Permission.SYSTEM_READ,
            Permission.AUDIT_READ,
            Permission.CASE_READ_ALL,
        }
    ),
}


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: frozenset[Role]
    tenant: str = "default"
    display_name: str = ""

    @property
    def permissions(self) -> frozenset[Permission]:
        perms: set[Permission] = set()
        for r in self.roles:
            perms |= ROLE_PERMISSIONS[r]
        return frozenset(perms)

    def can(self, perm: Permission) -> bool:
        return perm in self.permissions

    @property
    def role_names(self) -> list[str]:
        return sorted(r.value for r in self.roles)


SYSTEM = Principal("system", frozenset({Role.ADMIN}), "default", "Sentinel system")


class TokenService:
    def __init__(
        self,
        secret: str,
        algorithm: str = "HS256",
        ttl_minutes: int = 30,
        issuer: str = "sentinel-dd",
        is_revoked: object | None = None,
    ) -> None:
        if len(secret) < 32:
            raise ValueError("JWT secret must be at least 32 characters")
        self._secret = secret
        self._alg = algorithm
        self._ttl = ttl_minutes * 60
        self._issuer = issuer
        self._is_revoked = is_revoked  # callable(jti) -> bool, backed by the database

    def issue(
        self, subject: str, roles: list[Role] | list[str], tenant: str = "default", name: str = ""
    ) -> tuple[str, int]:
        now = int(time.time())
        payload = {
            "sub": subject,
            "roles": [Role(r).value for r in roles],
            "tenant": tenant,
            "name": name,
            "iat": now,
            "nbf": now,
            "exp": now + self._ttl,
            "iss": self._issuer,
            "jti": uuid.uuid4().hex,
        }
        return jwt.encode(payload, self._secret, algorithm=self._alg), now + self._ttl

    def decode_unverified_expiry(self, token: str) -> dict[str, object]:
        return jwt.decode(
            token, self._secret, algorithms=[self._alg], options={"verify_exp": False}, issuer=self._issuer
        )

    def verify(self, token: str) -> tuple[Principal, str, int]:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[self._alg],
                issuer=self._issuer,
                options={"require": ["exp", "sub", "iat", "jti", "iss"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("token expired") from exc
        except jwt.PyJWTError as exc:
            raise AuthError("invalid token") from exc
        if self._is_revoked is not None and self._is_revoked(claims["jti"]):  # type: ignore[operator]
            raise AuthError("token revoked")
        try:
            roles = frozenset(Role(r) for r in claims.get("roles", []))
        except ValueError as exc:
            raise AuthError("unknown role in token") from exc
        if not roles:
            raise AuthError("token carries no roles")
        return (
            Principal(claims["sub"], roles, claims.get("tenant", "default"), claims.get("name", "")),
            claims["jti"],
            int(claims["exp"]),
        )
