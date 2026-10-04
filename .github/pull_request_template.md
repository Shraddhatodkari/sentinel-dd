## What & why

## How it was tested
- [ ] `make check` passes (lint, types, tests, coverage >= 80%)
- [ ] `make eval` gates pass (RAG, agent routing, prompt-injection)

## Risk & governance
- [ ] No new PII flows, or DLP applied
- [ ] Prompt changes bumped `PROMPT_VERSION` and re-ran eval
- [ ] Model changes went through `sentinel-train` quality gates
