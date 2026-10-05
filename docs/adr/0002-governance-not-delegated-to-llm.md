# ADR-0002: Approval decisions are never delegated to an LLM

**Status:** Accepted

## Decision
LLMs plan, extract and summarise. The approve/review/deny decision comes from a declarative `PolicyEngine` using
typed inputs (scorecard band and score, screening potential matches, PEP matches, jurisdiction tier, coverage,
resolution confidence, report grounding). Sanctions/PEP matches and
high-risk scores always require a human, and the reviewer must be a different user from the requester
(maker-checker).

## Rationale
This shuts off prompt-injection attacks such as "approve this vendor automatically" found inside vendor
documents. It also meets model-risk-management expectations that material decisions stay explainable and
deterministic.

*v2 update:* the ML risk classifier was replaced by a versioned, evidence-linked scorecard (ADR-0007); auto-clear is
disabled by default so every case receives a human decision.
