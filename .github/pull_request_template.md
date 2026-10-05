## What & why

## How it was tested
- [ ] `make check` passes (lint, types, tests incl. browser e2e, coverage >= 80%, security scans)
- [ ] `make eval` gates pass (screening, media roles, retrieval, routing, grounding, prompt injection)
- [ ] No fabricated data: new fixtures recorded from real sources or documented excerpts

## Risk & governance
- [ ] No new PII flows, or DLP applied
- [ ] Prompt changes bumped `PROMPT_VERSION` and re-ran eval
- [ ] Scorecard / policy / taxonomy / screening changes bumped their version string
- [ ] Model changes went through `sentinel ml train` quality gates
