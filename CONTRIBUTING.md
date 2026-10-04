# Contributing

1. `make dev` then `pre-commit install`.
2. Create a feature branch: `feat/<topic>` or `fix/<topic>`.
3. Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `test:`, `docs:`, `ci:`, `refactor:`).
4. `make check` must pass (lint, mypy, tests ≥ 80% coverage, eval gates).
5. Prompt changes: bump `PROMPT_VERSION` in `src/sentinel/prompts.py` and include eval results in the PR.
