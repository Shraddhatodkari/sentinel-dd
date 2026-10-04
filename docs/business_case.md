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

1. **Straight-through processing of clean, low-risk vendors.** The policy engine auto-approves only when every
   check passes.
2. **Analyst augmentation for complex cases.** The analyst starts from a cited draft memo, a UBO graph and
   screening hits instead of a blank page.
3. **Better detection** of indirect exposure: k-hop sanctions paths and offshore layering.
4. **Lower audit cost** through the hash-chained audit trail, versioned prompts and models, and stored rationales.

## KPI framework (measure in a pilot; values here are targets, not claims)

| KPI | Definition | Pilot target |
|---|---|---|
| Median case cycle time | case created → final decision | ↓ 50% vs. baseline |
| Straight-through rate | % auto-approved with no later reversal | 30–50% of low-risk population |
| Reviewer agreement | % of AI recommendations upheld by reviewers | ≥ 85% |
| Missed-risk rate | critical findings found later that Sentinel missed | ≤ baseline manual rate |
| Grounding score | share of memo claims supported by cited evidence | ≥ 0.9 |
| Cost per case | LLM + infra cost | tracked per case via token metrics |

## Rollout plan

1. **Shadow mode** (4–6 weeks): Sentinel runs alongside analysts. Compare outputs and calibrate thresholds.
2. **Assisted mode**: analysts use the draft memos, and every decision still goes through a human.
3. **Straight-through for LOW risk**, with sampling QA (for example 10% re-review) and drift monitoring.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Hallucinated facts | Grounding checks, mandatory citations, abstention, critic loop, human review below threshold |
| Prompt injection via vendor documents | Context sanitisation, injection scoring, the LLM has no approval authority |
| Model drift | PSI monitoring, champion/challenger with quality gates |
| PII exposure | DLP before indexing and in outputs, tokenisation vault, RBAC, network policy |
| Over-reliance on automation | Maker-checker, policy-forced HITL for sanctions/PEP/high risk |
