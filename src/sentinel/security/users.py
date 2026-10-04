"""User accounts (local authentication). Production deployments should front this with the corporate IdP (OIDC/SAML)
and disable local auth; roles and tenants are then mapped from IdP claims."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select

from sentinel.db.base import Database
from sentinel.db.models import User
from sentinel.security.auth import Principal, Role
from sentinel.security.passwords import DUMMY_HASH, hash_password, password_problems, verify_password
from sentinel.timeutil import aware, iso, utcnow

MAX_FAILED = 5
LOCKOUT = timedelta(minutes=15)


class UserError(Exception):
    pass


class UserService:
    def __init__(self, db: Database, min_password_length: int = 12) -> None:
        self.db = db
        self.min_len = min_password_length

    def create(
        self, username: str, password: str, roles: list[str], tenant: str = "default", display_name: str = ""
    ) -> dict[str, Any]:
        if not username.isascii() or not username.replace("_", "").replace(".", "").replace("-", "").isalnum():
            raise UserError("username may contain letters, digits, '.', '_' and '-' only")
        problems = password_problems(password, self.min_len, username)
        if problems:
            raise UserError("password " + "; ".join(problems))
        parsed = [Role(r) for r in roles]
        if not parsed:
            raise UserError("at least one role is required")
        with self.db.session() as s:
            if s.scalars(select(User).where(User.username == username)).first():
                raise UserError(f"user {username} already exists")
            u = User(
                username=username,
                display_name=display_name or username,
                password_hash=hash_password(password),
                roles=[r.value for r in parsed],
                tenant=tenant,
            )
            s.add(u)
            s.flush()
            return self._dict(u)

    def authenticate(self, username: str, password: str) -> Principal | None:
        with self.db.session() as s:
            u = s.scalars(select(User).where(User.username == username)).first()
            if u is None:
                verify_password(password, DUMMY_HASH)  # equalise timing for unknown users
                return None
            if not u.is_active:
                return None
            if u.locked_until and aware(u.locked_until) > utcnow():  # type: ignore[operator]
                return None
            if not verify_password(password, u.password_hash):
                u.failed_logins += 1
                if u.failed_logins >= MAX_FAILED:
                    u.locked_until, u.failed_logins = utcnow() + LOCKOUT, 0
                return None
            u.failed_logins, u.locked_until = 0, None
            return Principal(u.username, frozenset(Role(r) for r in u.roles), u.tenant, u.display_name)

    def is_locked(self, username: str) -> bool:
        with self.db.session() as s:
            u = s.scalars(select(User).where(User.username == username)).first()
            return bool(u and u.locked_until and aware(u.locked_until) > utcnow())  # type: ignore[operator]

    def get_principal(self, username: str) -> Principal | None:
        with self.db.session() as s:
            u = s.scalars(select(User).where(User.username == username, User.is_active.is_(True))).first()
            return (
                None
                if u is None
                else Principal(u.username, frozenset(Role(r) for r in u.roles), u.tenant, u.display_name)
            )

    def list(self, tenant: str) -> list[dict[str, Any]]:
        with self.db.session() as s:
            return [self._dict(u) for u in s.scalars(select(User).where(User.tenant == tenant).order_by(User.username))]

    def set_active(self, username: str, active: bool) -> None:
        with self.db.session() as s:
            u = s.scalars(select(User).where(User.username == username)).first()
            if u is None:
                raise UserError("no such user")
            u.is_active = active

    @staticmethod
    def _dict(u: User) -> dict[str, Any]:
        return {
            "username": u.username,
            "display_name": u.display_name,
            "roles": u.roles,
            "tenant": u.tenant,
            "is_active": u.is_active,
            "created_at": iso(u.created_at),
        }
