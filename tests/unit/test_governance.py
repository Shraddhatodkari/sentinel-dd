import pytest

from sentinel.governance.hitl import ApprovalError, ApprovalQueue, ApprovalStatus
from sentinel.governance.policy import ActionContext, Decision, PolicyEngine
from sentinel.security.audit import AuditLog


@pytest.fixture
def engine():
    return PolicyEngine(hitl_risk_threshold=0.6, min_grounding=0.35)


def test_low_risk_allowed(engine):
    assert engine.evaluate(ActionContext("finalize", risk_score=0.1)).decision == Decision.ALLOW


@pytest.mark.parametrize(
    "ctx",
    [
        ActionContext("x", risk_score=0.65),
        ActionContext("x", sanctions_hit=True),
        ActionContext("x", pep_hit=True),
        ActionContext("x", anomaly=True),
        ActionContext("x", grounding_score=0.1),
        ActionContext("recommend_onboard", risk_score=0.45),
    ],
)
def test_requires_approval(engine, ctx):
    res = engine.evaluate(ctx)
    assert res.decision == Decision.REQUIRE_APPROVAL and res.reasons


def test_deny_rules(engine):
    assert engine.evaluate(ActionContext("x", jurisdiction="kp")).decision == Decision.DENY
    assert engine.evaluate(ActionContext("external_call", pii_severity="critical")).decision == Decision.DENY


def test_hitl_lifecycle_and_maker_checker():
    audit = AuditLog()
    q = ApprovalQueue(audit)
    req = q.submit("case-1", "alice", ["high risk"], {"risk": 0.9})
    assert q.pending() == [req] and q.for_case("case-1") == [req]
    with pytest.raises(ApprovalError, match="segregation"):
        q.decide(req.id, "alice", True)
    done = q.decide(req.id, "bob", True, "ok")
    assert done.status == ApprovalStatus.APPROVED and done.decided_by == "bob"
    with pytest.raises(ApprovalError, match="already"):
        q.decide(req.id, "bob", False)
    with pytest.raises(ApprovalError, match="not found"):
        q.decide("nope", "bob", True)
    with pytest.raises(ApprovalError):
        q.get("nope")
    assert [r["action"] for r in audit.records()] == ["approval.submitted", "approval.approved"]
    assert q.get(req.id).to_dict()["status"] == "approved"
