# Business Case: Cutting Vendor Risk Checks from 3 Weeks to 3 Days with AI

**Project:** Sentinel DD — third-party risk and sanctions screening
**Author:** Shraddha Todkari

> All figures below are illustrative estimates based on stated assumptions, not client data.

## Recommendation

Use AI for data collection, sanctions screening and first-draft risk reports, and keep human analysts for judgement and sign-off. Pilot on low-risk vendors first, then scale.

## The problem

A large bank onboards about 500 vendors a year (IT providers, payment partners, outsourcing firms). Before signing, every vendor must be checked for sanctions, ownership, financial health, legal cases and adverse news. Today this takes about 3 weeks per vendor and is mostly manual. The business finds onboarding slow; the regulator expects strong, auditable checks.

## Why it is slow (issue tree)

1. **Data collection**
   - Sanctions lists, company registries, court records and news searched one by one
   - Results copied into Excel by hand
2. **Risk analysis**
   - Name-matching errors ("ABC Pvt Ltd" vs "A.B.C. Private Limited") create false alarms
   - Every vendor gets the same deep review, even low-risk ones
3. **Reporting and approval**
   - Each report written from scratch
   - Many hand-offs between procurement, risk and compliance

## Impact estimate

| Item | Today | With AI |
| --- | --- | --- |
| Analyst hours per vendor | 15 | 6 |
| Vendors per year | 500 | 500 |
| Total analyst hours per year | 7,500 | 3,000 |
| Time to onboard a low-risk vendor | ~3 weeks | ~3 days |

**About 4,500 analyst hours saved per year**, or roughly 2–3 analysts moved to high-risk cases.

## Implementation plan

1. **Tier vendors by risk** (low / medium / high) so low-risk vendors get a fast, automated check.
2. **Automate screening** against official sanctions lists with fuzzy name matching.
3. **Use AI agents** to read news and filings and draft the red-flag summary.
4. **Human sign-off** on every report, with a full audit trail for the regulator.
5. **Pilot for 3 months** on 50 low-risk vendors; measure time saved, accuracy and missed flags before scaling.

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Missed matches (false negatives) | Fuzzy matching tuned for recall; analyst review of every report |
| Data privacy | Private hosting; no vendor data used to train external models |
| Regulator acceptance | Full audit log of sources, matches and decisions |

## How Sentinel DD supports this

Sentinel DD demonstrates steps 2–4 of the plan: a governed multi-agent workflow that resolves the legal entity, collects evidence from official sources, screens it, scores risk with an explainable scorecard and routes every case to a human reviewer.

Validated end to end on live public data:

- **33,649** official list records
- **100% screening recall** on 224 perturbed list names
- **0.9% false alarms** on 1,000 real SEC registrants
- **114 automated tests** at 94.8% coverage

See the [README](README.md) for architecture and evidence.
