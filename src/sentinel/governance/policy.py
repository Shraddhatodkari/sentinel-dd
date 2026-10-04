"""Declarative governance policy engine.

Decides, for every agent-proposed action, whether it may proceed autonomously, needs a human
in the loop, or is denied outright. Rules are data (easily externalised to YAML/OPA later)
and every decision carries machine-readable reasons for the audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Decision(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass
class PolicyResult:
    decision: Decision
    reasons: list[str] = field(default_factory=list)


@dataclass
class ActionContext:
    action: str  # e.g. "finalize_report", "recommend_onboard", "external_call"
    risk_score: float = 0.0
    sanctions_hit: bool = False
    pep_hit: bool = False
    anomaly: bool = False
    grounding_score: float = 1.0
    pii_severity: str | None = None
    jurisdiction: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)


HIGH_RISK_JURISDICTIONS = frozenset({"KP", "IR", "MM"})  # FATF "call for action" list (illustrative)


class PolicyEngine:
    def __init__(
        self,
        hitl_risk_threshold: float = 0.6,
        min_grounding: float = 0.35,
        deny_jurisdictions: frozenset[str] = HIGH_RISK_JURISDICTIONS,
    ) -> None:
        self.hitl_risk_threshold = hitl_risk_threshold
        self.min_grounding = min_grounding
        self.deny_jurisdictions = deny_jurisdictions

    def evaluate(self, ctx: ActionContext) -> PolicyResult:
        deny: list[str] = []
        review: list[str] = []
        if ctx.jurisdiction and ctx.jurisdiction.upper() in self.deny_jurisdictions:
            deny.append(f"jurisdiction {ctx.jurisdiction} is prohibited")
        if ctx.pii_severity == "critical" and ctx.action == "external_call":
            deny.append("critical PII may not leave the trust boundary")
        if ctx.sanctions_hit:
            review.append("potential sanctions match requires human adjudication")
        if ctx.pep_hit:
            review.append("politically exposed person requires enhanced due diligence")
        if ctx.risk_score >= self.hitl_risk_threshold:
            review.append(f"risk score {ctx.risk_score:.2f} >= threshold {self.hitl_risk_threshold:.2f}")
        if ctx.anomaly:
            review.append("anomaly detector flagged unusual profile")
        if ctx.grounding_score < self.min_grounding:
            review.append(f"low grounding score {ctx.grounding_score:.2f}")
        if ctx.action == "recommend_onboard" and ctx.risk_score >= 0.4:
            review.append("onboarding recommendation for medium+ risk entity")

        if deny:
            return PolicyResult(Decision.DENY, deny + review)
        if review:
            return PolicyResult(Decision.REQUIRE_APPROVAL, review)
        return PolicyResult(Decision.ALLOW, ["all policy checks passed"])
