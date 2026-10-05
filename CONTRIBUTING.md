# Contributing

1. Set up: see [docs/development.md](docs/development.md); `pre-commit install`.
2. Branch: `feat/<topic>` or `fix/<topic>`; [Conventional Commits](https://www.conventionalcommits.org/).
3. `make check` must pass (lint, types, tests with ≥ 80 % coverage, evaluation gates, security scans).
4. Data: never add fabricated source data. New fixtures are recorded from the real source
   (`scripts/record_fixtures.py`) or documented excerpts (`scripts/make_watchlist_excerpts.py`).
5. Schema changes: new Alembic revision. Scorecard/policy/taxonomy/prompt changes: bump the version string and attach
   `sentinel eval` output to the PR.
