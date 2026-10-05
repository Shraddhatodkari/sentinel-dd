# Business Case: AI-Assisted Third-Party Due Diligence

## Problem statement

Regulated organisations must run due diligence on vendors, suppliers and corporate customers before onboarding
them, and again periodically afterwards. The work is document-heavy and repetitive, but it carries high stakes:
missing a sanctioned beneficial owner can lead to regulatory penalties and reputational damage. Manual reviews
are slow, vary between analysts, and are hard to evidence to auditors.

## Stakeholders

| Stakeholder | Need |
|---|---|
| Compliance analysts | Less manual research, a consistent memo format |
| Compliance reviewers / TPRM committee | Clear reasons why a case needs their decision; quick approve/reject |
| Procurement / business owners | Faster onboarding of low-risk vendors |
| Internal audit / regulators | Evidence: who decided what, when, on which data and model version |
| CISO / DPO | No PII leakage to LLM providers or reports; access control |

## Solution value levers

1. **Faster clean cases.** Low-risk cases arrive at the reviewer with evidence, coverage and the scorecard already
   assembled. Auto-clearing exists in the policy engine but is disabled by default; enable it only after a shadow
   period and with QA sampling.
2. **Analyst augmentation for complex cases.** The analyst starts from a cited draft memo, a UBO graph and
   screening hits instead of a blank page.
3. **Better detection** of indirect exposure: related-party screening (consolidation parents, subsidiaries, insiders)
   and exposure paths in the relationship graph.
4. **Lower audit cost** through the hash-chained audit trail, versioned prompts and models, and stored rationales.

## KPI framework (measure in a pilot; values here are targets, not claims)

| KPI | Definition | Pilot target |
|---|---|---|
| Median case cycle time | case created → final decision | ↓ 50% vs. baseline |
| Reviewer time on LOW cases | median minutes from case open to decision for LOW band | ↓ vs. baseline (measure in shadow mode) |
| Reviewer agreement | % of AI recommendations upheld by reviewers | ≥ 85% |
| Missed-risk rate | critical findings found later that Sentinel missed | ≤ baseline manual rate |
| Grounding score | share of memo claims supported by cited evidence | ≥ 0.9 |
| Cost per case | LLM + infra cost | tracked per case via token metrics |

## Rollout plan

1. **Shadow mode** (4–6 weeks): Sentinel runs alongside analysts. Compare outputs and calibrate thresholds.
2. **Assisted mode**: analysts use the draft memos, and every decision still goes through a human.
3. **Optional auto-clear for LOW risk** (configuration change, policy version bump), with sampling QA (for example
   10 % re-review) and drift monitoring.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Hallucinated facts | Default extractive backend (no generation), grounding checks, mandatory citations, abstention, critic loop, review on low grounding |
| Prompt injection via vendor documents | Context sanitisation, injection scoring, the LLM has no approval authority |
| Model drift | PSI monitoring (`sentinel ml drift`), quality gates before promotion |
| PII exposure | DLP on reports, input guardrails, RBAC, tenant isolation, network policy |
| Over-reliance on automation | Maker-checker, policy-forced HITL for sanctions/PEP/high risk |
