"""Declarative third-party-risk policy engine (versioned rules with stable IDs).

Outcomes:
  AUTO_CLEAR — every rule passed and auto-clearing is enabled for this deployment
  REVIEW     — a reviewer (role ``reviewer`` or ``senior_reviewer``) must decide
  ESCALATE   — a ``senior_reviewer`` must decide (sanctions, high risk, prohibited jurisdiction)

The policy engine — never an LLM — decides the routing. The policy *recommends*; the human *decides*.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Outcome(StrEnum):
    AUTO_CLEAR = "AUTO_CLEAR"
    REVIEW = "REVIEW"
    ESCALATE = "ESCALATE"


ROLE_FOR = {Outcome.REVIEW: "reviewer", Outcome.ESCALATE: "senior_reviewer"}
_RANK = {Outcome.AUTO_CLEAR: 0, Outcome.REVIEW: 1, Outcome.ESCALATE: 2}


@dataclass
class RuleResult:
    rule_id: str
    description: str
    outcome: Outcome
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class PolicyInput:
    band: str
    score: float
    confidence: float
    sanctions_subject: list[dict[str, Any]]
    sanctions_related: list[dict[str, Any]]
    pep_matches: list[dict[str, Any]]
    jurisdiction_tier: str
    resolution_confidence: float
    ambiguous: bool
    resolved: bool
    coverage: dict[str, str]
    stale_sources: list[str]
    report_grounding: float
    guardrail_violations: list[str]


@dataclass
class PolicyResult:
    version: str
    outcome: Outcome
    required_role: str | None
    rules: list[RuleResult]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["outcome"] = self.outcome.value
        for r in d["rules"]:
            r["outcome"] = str(r["outcome"])
        return d


class PolicyEngine:
    def __init__(
        self,
        version: str,
        auto_clear_enabled: bool = False,
        min_confidence: float = 0.6,
        min_grounding: float = 0.6,
        min_resolution_confidence: float = 0.85,
    ) -> None:
        self.version = version
        self.auto_clear_enabled = auto_clear_enabled
        self.min_confidence = min_confidence
        self.min_grounding = min_grounding
        self.min_resolution_confidence = min_resolution_confidence

    def evaluate(self, p: PolicyInput) -> PolicyResult:
        fired: list[RuleResult] = []

        def ev(items: list[dict[str, Any]]) -> list[str]:
            return sorted({e for i in items for e in i.get("evidence_ids", [])})

        if p.sanctions_subject:
            fired.append(
                RuleResult(
                    "TPRM-SAN-01",
                    f"{len(p.sanctions_subject)} sanctions potential match(es) on the "
                    "subject — mandatory adjudication by a senior reviewer",
                    Outcome.ESCALATE,
                    ev(p.sanctions_subject),
                )
            )
        if p.sanctions_related:
            fired.append(
                RuleResult(
                    "TPRM-SAN-02",
                    f"{len(p.sanctions_related)} sanctions potential match(es) on related "
                    "parties (parents/subsidiaries/insiders)",
                    Outcome.ESCALATE,
                    ev(p.sanctions_related),
                )
            )
        if p.jurisdiction_tier == "prohibited":
            fired.append(
                RuleResult(
                    "TPRM-JUR-01",
                    "entity is in a prohibited jurisdiction under the jurisdiction policy",
                    Outcome.ESCALATE,
                )
            )
        elif p.jurisdiction_tier == "high":
            fired.append(
                RuleResult(
                    "TPRM-JUR-02", "entity is in a high-risk jurisdiction — enhanced due diligence", Outcome.REVIEW
                )
            )
        if p.band == "HIGH":
            fired.append(RuleResult("TPRM-RSK-01", f"risk band HIGH (score {p.score:.1f})", Outcome.ESCALATE))
        elif p.band == "MEDIUM":
            fired.append(RuleResult("TPRM-RSK-02", f"risk band MEDIUM (score {p.score:.1f})", Outcome.REVIEW))
        if p.pep_matches:
            fired.append(
                RuleResult(
                    "TPRM-PEP-01",
                    f"{len(p.pep_matches)} PEP potential match(es) — enhanced due diligence required",
                    Outcome.REVIEW,
                    ev(p.pep_matches),
                )
            )
        if not p.resolved:
            fired.append(
                RuleResult(
                    "TPRM-DAT-01", "entity could not be resolved to an authoritative registry record", Outcome.REVIEW
                )
            )
        elif p.ambiguous or p.resolution_confidence < self.min_resolution_confidence:
            fired.append(
                RuleResult(
                    "TPRM-DAT-02",
                    f"entity resolution uncertain (confidence {p.resolution_confidence:.2f}"
                    f"{', ambiguous' if p.ambiguous else ''})",
                    Outcome.REVIEW,
                )
            )
        if p.coverage.get("screening") not in ("ok",) or p.stale_sources:
            fired.append(
                RuleResult(
                    "TPRM-DAT-03",
                    "sanctions/PEP screening coverage incomplete or stale: "
                    + (", ".join(p.stale_sources) or p.coverage.get("screening", "missing")),
                    Outcome.REVIEW,
                )
            )
        missing = [k for k, v in p.coverage.items() if v == "unavailable"]
        if missing:
            fired.append(
                RuleResult("TPRM-DAT-04", f"data sources unavailable: {', '.join(sorted(missing))}", Outcome.REVIEW)
            )
        if p.confidence < self.min_confidence:
            fired.append(
                RuleResult(
                    "TPRM-DAT-05",
                    f"assessment confidence {p.confidence:.2f} below {self.min_confidence}",
                    Outcome.REVIEW,
                )
            )
        if p.report_grounding < self.min_grounding:
            fired.append(
                RuleResult(
                    "TPRM-AI-01",
                    f"report grounding {p.report_grounding:.2f} below {self.min_grounding}",
                    Outcome.REVIEW,
                )
            )
        if any(v.startswith("prohibited") for v in p.guardrail_violations):
            fired.append(RuleResult("TPRM-AI-02", "generated narrative violated output guardrails", Outcome.REVIEW))
        if not fired and not self.auto_clear_enabled:
            fired.append(
                RuleResult(
                    "TPRM-GOV-01",
                    "all checks passed; deployment policy requires human sign-off for every onboarding decision",
                    Outcome.REVIEW,
                )
            )
        outcome = max((r.outcome for r in fired), key=_RANK.__getitem__, default=Outcome.AUTO_CLEAR)
        return PolicyResult(self.version, outcome, ROLE_FOR.get(outcome), fired)
