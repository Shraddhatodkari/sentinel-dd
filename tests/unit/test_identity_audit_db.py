import time

import jwt
import pytest
from sqlalchemy import select, text

from sentinel.db.migrate import current_revision, head_revision
from sentinel.db.models import Case, Review
from sentinel.security.audit import AuditLog
from sentinel.security.auth import ROLE_PERMISSIONS, AuthError, Permission, Principal, Role, TokenService
from sentinel.security.passwords import hash_password, password_problems, verify_password
from sentinel.security.users import UserError, UserService

SECRET = "x" * 40


def test_least_privilege_role_matrix():
    P = Permission
    assert ROLE_PERMISSIONS[Role.ANALYST] == {P.CASE_CREATE, P.CASE_READ_OWN, P.SEARCH}
    assert P.REVIEW_DECIDE not in ROLE_PERMISSIONS[Role.ANALYST]
    assert P.CASE_CREATE not in ROLE_PERMISSIONS[Role.REVIEWER]  # maker ≠ checker by construction
    assert P.REVIEW_DECIDE_ESCALATED in ROLE_PERMISSIONS[Role.SENIOR_REVIEWER]
    assert P.REVIEW_DECIDE_ESCALATED not in ROLE_PERMISSIONS[Role.REVIEWER]
    assert not ROLE_PERMISSIONS[Role.ADMIN] & {P.REVIEW_DECIDE, P.CASE_CREATE}  # admins make no business decisions
    assert ROLE_PERMISSIONS[Role.AUDITOR] == {P.CASE_READ_ALL, P.AUDIT_READ, P.SYSTEM_READ}


def test_tokens_issue_verify_revoke_and_reject():
    revoked: set[str] = set()
    ts = TokenService(SECRET, ttl_minutes=5, is_revoked=lambda j: j in revoked)
    tok, exp = ts.issue("alice", [Role.ANALYST], "t1", "Alice")
    p, jti, exp2 = ts.verify(tok)
    assert (p.subject, p.tenant, p.display_name, exp2) == ("alice", "t1", "Alice", exp)
    revoked.add(jti)
    with pytest.raises(AuthError, match="revoked"):
        ts.verify(tok)
    with pytest.raises(AuthError):
        ts.verify(jwt.encode({"sub": "x"}, "y" * 40, algorithm="HS256"))
    now = int(time.time())
    forged = jwt.encode(
        {"sub": "m", "roles": ["superuser"], "iat": now, "exp": now + 60, "jti": "j", "iss": "sentinel-dd"},
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(AuthError, match="unknown role"):
        ts.verify(forged)
    empty = jwt.encode(
        {"sub": "m", "roles": [], "iat": now, "exp": now + 60, "jti": "k", "iss": "sentinel-dd"},
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(AuthError, match="no roles"):
        ts.verify(empty)
    expired = jwt.encode(
        {"sub": "m", "roles": ["analyst"], "iat": now - 100, "exp": now - 10, "jti": "e", "iss": "sentinel-dd"},
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(AuthError, match="expired"):
        ts.verify(expired)
    with pytest.raises(ValueError):
        TokenService("short")
    assert ts.decode_unverified_expiry(expired)["sub"] == "m"


def test_password_hashing_and_policy():
    h = hash_password("Correct-Horse-9!x", iterations=1000)
    assert verify_password("Correct-Horse-9!x", h) and not verify_password("wrong", h)
    assert not verify_password("x", "garbage") and not verify_password("x", "md5$1$a$b")
    assert password_problems("short") and password_problems("alllowercaseletters")
    assert "must not contain the username" in password_problems("Alice-Password-1", username="alice")[0]
    assert password_problems("Correct-Horse-9!x") == []


def test_user_service_lockout_and_validation(container):
    us = UserService(container.db)
    with pytest.raises(UserError):
        us.create("bad name!", "Correct-Horse-9!x", ["analyst"])
    with pytest.raises(UserError):
        us.create("weak", "password", ["analyst"])
    with pytest.raises(UserError, match="exists"):
        us.create("alice", "Correct-Horse-9!x", ["analyst"])
    with pytest.raises(UserError):
        us.create("norole", "Correct-Horse-9!x", [])
    assert us.authenticate("alice", "Correct-Horse-9!x").subject == "alice"
    assert us.authenticate("ghost", "x") is None
    for _ in range(5):
        assert us.authenticate("mallory", "wrong-password") is None
    assert us.is_locked("mallory")
    assert us.authenticate("mallory", "Correct-Horse-9!x") is None  # locked even with the right password
    us.set_active("alice", False)
    assert us.authenticate("alice", "Correct-Horse-9!x") is None and us.get_principal("alice") is None
    assert {u["username"] for u in us.list("default")} >= {"bob", "carol"}
    with pytest.raises(UserError):
        us.set_active("ghost", True)


def test_audit_chain_append_only_and_tamper_detection(make_db):
    db = make_db("a.db")
    log = AuditLog(db)
    for i in range(6):
        rec = log.append("alice", "case.created", "case", f"c{i}", {"i": i}, roles=["analyst"])
    assert rec["seq"] == 5 and log.verify() == {
        "intact": True,
        "records": 6,
        "first_bad_seq": None,
        "head_matches": True,
    }
    assert log.count() == 6 and len(log.records(resource_id="c1")) == 1 and len(log.records(before_seq=2)) == 2
    with pytest.raises(Exception, match="append-only"), db.engine.begin() as c:
        c.execute(text("UPDATE audit_events SET actor='eve' WHERE seq=2"))
    with pytest.raises(Exception, match="append-only"), db.engine.begin() as c:
        c.execute(text("DELETE FROM audit_events WHERE seq=2"))
    # a privileged attacker who drops the trigger is still detected by the hash chain
    with db.engine.begin() as c:
        c.execute(text("DROP TRIGGER audit_events_no_update"))
        c.execute(text("UPDATE audit_events SET details='{\"i\": 99}' WHERE seq=3"))
    res = log.verify()
    assert not res["intact"] and res["first_bad_seq"] == 3


def test_migrations_constraints(make_db):
    db = make_db("m.db")
    assert current_revision(db.engine) == head_revision(db.engine) == "0002_revoked_tokens"
    from alembic import command

    from sentinel.db.migrate import alembic_config

    command.downgrade(alembic_config(db.engine), "base")
    assert current_revision(db.engine) is None
    db.upgrade()
    with db.session() as s:
        s.add(Case(id="c1", entity_query="x", created_by="alice"))
    with pytest.raises(Exception, match=r"ck_review_maker_checker|CHECK"), db.session() as s:
        s.add(
            Review(
                case_id="c1",
                tier="REVIEW",
                required_role="reviewer",
                requested_by="alice",
                decided_by="alice",
                status="approved",
            )
        )
    with pytest.raises(Exception, match=r"ck_case_status|CHECK"), db.session() as s:
        s.add(Case(id="c2", entity_query="x", created_by="a", status="bogus"))
    with db.session() as s:
        assert s.scalars(select(Case)).one().status == "queued"
    assert db.ping() and Principal("x", frozenset({Role.ADMIN})).role_names == ["admin"]
