# Developer setup

## Prerequisites

Python 3.11–3.13, Node 20+ (22 used in CI), git. Optional: Docker, PostgreSQL 16, Chromium for browser e2e.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"            # + playwright for browser e2e:  pip install playwright && python -m playwright install chromium
pip install torch --index-url https://download.pytorch.org/whl/cpu   # only for the fine-tuning research tests
(cd dashboard && npm ci && npm run build)
pre-commit install
```

## Everyday commands

| Command | What |
|---|---|
| `make lint` / `make format` | ruff check / format (src, tests, scripts) |
| `make type` | mypy (src + scripts), configuration-docs drift check, `tsc` for the dashboard |
| `make test` / `make cov` | pytest (offline) / with coverage (gate 80 %) |
| `make e2e` | browser test of the dashboard |
| `make eval` | offline evaluation gates |
| `make security` | bandit, pip-audit, npm audit |
| `make serve` | API + dashboard on :8000 |
| `cd dashboard && npm run dev` | dashboard dev server on :5173 with API proxy |

## Tests

* `tests/unit`, `tests/integration`, `tests/e2e/test_api.py` — offline, deterministic. A session fixture builds a seeded
  workspace once (migrated SQLite, list excerpts loaded, peer model trained on recorded SEC frames, test users) and
  copies it per test.
* `tests/e2e/test_dashboard.py` — real Chromium against the built SPA served by uvicorn; skipped if the build or
  Playwright is missing. Fails on any browser console error other than expected 4xx responses (so CSP violations and script errors fail it).
* `tests/live` — `pytest -m live` with `SENTINEL_SEC_USER_AGENT` set; hits the real upstream sources.

### Recording fixtures

All HTTP in offline tests goes through `ReplayTransport`. The offline suite and `sentinel eval` use it with
`strict=False`: a request without a recording is answered with HTTP 503, which the system treats like an upstream
outage (degraded coverage) — never with made-up content. With the default `strict=True` it raises instead. To add a scenario:

```bash
python scripts/record_fixtures.py --out tests/fixtures/http "Example Corp"     # records real responses (network)
python scripts/make_watchlist_excerpts.py --raw-dir /path/to/full/lists --out tests/fixtures/http
```

Never hand-edit cassette bodies; never add fabricated responses. Cassettes store the URL, status, content type,
retrieval time and body hash (excerpts additionally store the full file hash and selection rule).

## Conventions

* Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `build:`, `ci:`, `refactor:`).
* Schema changes only through a new Alembic revision in `src/sentinel/db/migrations/versions`.
* Scorecard / policy / taxonomy / prompt changes bump their version string and include `sentinel eval` output in
  the PR.
* New graph nodes need an `AgentSpec` in `agents/specs.py` (a test enforces this).
