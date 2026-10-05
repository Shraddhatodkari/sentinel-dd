# Configuration

All settings are environment variables with the `SENTINEL_` prefix (or entries in a `.env` file in the working
directory), parsed and type-checked by `sentinel.config.Settings` (pydantic-settings). Secrets are `SecretStr` and
never logged. `.env.example` lists the settings most deployments change.

With `SENTINEL_ENV=prod` the service refuses to start if the JWT secret is the development value or shorter than 32
characters, if the database is SQLite, or if the SEC User-Agent still contains a placeholder domain.

Additional variables outside `Settings`:

| Variable | Used by | Notes |
|---|---|---|
| `SENTINEL_DASHBOARD_DIR` | API | directory of the built dashboard (default `dashboard/dist` in a checkout; `/app/dashboard` in the image) |
| `SENTINEL_JURISDICTION_POLICY_PATH` | risk / policy | JSON file overriding the default jurisdiction tiers (see `risk/jurisdictions.py`) |
| `SENTINEL_NEW_PASSWORD` | CLI `users create` | avoids an interactive prompt |
| `SENTINEL_MIGRATE_ON_START` | entrypoint + app | `false` when migrations run as a separate job |
| `SENTINEL_WEB_WORKERS`, `PORT` | container entrypoint | uvicorn worker processes and port |
| `POSTGRES_PASSWORD` | docker-compose | database password for the compose stack |

## Reference

| Variable | Default | Notes |
|---|---|---|
| `SENTINEL_ENV` | `dev` | `prod` enables start-up validation (strong JWT secret, PostgreSQL, real SEC contact) |
| `SENTINEL_LOG_LEVEL` | `INFO` |  |
| `SENTINEL_DATA_DIR` | `var` | raw source store, checkpoints, model registry (mount a volume in prod) |
| `SENTINEL_DATABASE_URL` | `sqlite:///var/sentinel.db` | prod: postgresql+psycopg://user:pass@host/db |
| `SENTINEL_CHECKPOINT_URL` | `var/checkpoints.sqlite` | LangGraph checkpointer (sqlite path or postgres DSN) |
| `SENTINEL_DB_ECHO` | `False` |  |
| `SENTINEL_MIGRATE_ON_START` | `True` | multi-replica deployments: false + run `sentinel db upgrade` as a Job |
| `SENTINEL_LLM_PROVIDER` | `extractive` | `extractive` = no external model; `anthropic` / `openai` / `bedrock` / `vertex` |
| `SENTINEL_LLM_FALLBACKS` | `[]` | JSON list of providers tried in order if the primary fails; extractive is always last |
| `SENTINEL_ANTHROPIC_API_KEY` | `—` | secret |
| `SENTINEL_ANTHROPIC_MODEL` | `claude-sonnet-4-5` |  |
| `SENTINEL_OPENAI_API_KEY` | `—` | secret |
| `SENTINEL_OPENAI_MODEL` | `gpt-4o-mini` |  |
| `SENTINEL_BEDROCK_MODEL_ID` | `anthropic.claude-3-5-sonnet-20240620-v1:0` |  |
| `SENTINEL_AWS_REGION` | `us-east-1` |  |
| `SENTINEL_VERTEX_PROJECT` | `—` |  |
| `SENTINEL_VERTEX_LOCATION` | `us-central1` |  |
| `SENTINEL_VERTEX_MODEL` | `gemini-1.5-pro` |  |
| `SENTINEL_LLM_MAX_TOKENS` | `1024` | ceiling on output tokens per LLM call |
| `SENTINEL_LLM_TIMEOUT_S` | `60.0` |  |
| `SENTINEL_LLM_MAX_RETRIES` | `3` |  |
| `SENTINEL_LLM_PRICE_PER_MTOK` | `{'claude-sonnet-4-5': (3.0, 15.0), 'gpt-4o-mini': (0.15, 0.6)}` | USD per 1M tokens (input, output) for cost metrics |
| `SENTINEL_SEC_USER_AGENT` | `Sentinel-DD compliance-research admin@example.com` | SEC requires a contact UA |
| `SENTINEL_HTTP_TIMEOUT_S` | `30.0` |  |
| `SENTINEL_HTTP_MAX_RETRIES` | `3` |  |
| `SENTINEL_HTTP_BACKOFF_BASE_S` | `1.0` |  |
| `SENTINEL_HTTP_RATE_LIMITS_ENABLED` | `True` | honour published fair-access intervals (SEC 10 req/s, GDELT 1/5 s) |
| `SENTINEL_SOURCE_CACHE_TTL_S` | `21600` | per-entity source responses re-used within this window |
| `SENTINEL_SANCTIONS_MAX_AGE_H` | `36` | sanctions-list freshness SLA (h); stale lists force review (TPRM-DAT-03) |
| `SENTINEL_PEP_MAX_AGE_H` | `336` | PEP-list freshness SLA (h) |
| `SENTINEL_ENABLE_GDELT` | `True` |  |
| `SENTINEL_ENABLE_OPENSANCTIONS_PEPS` | `False` | OpenSanctions is CC BY-NC: enable only with a commercial licence |
| `SENTINEL_EMBEDDING_BACKEND` | `hashing` | `hashing` (offline), `sentence_transformers` (extra `embeddings`), `openai` |
| `SENTINEL_EMBEDDING_DIM` | `384` |  |
| `SENTINEL_CHUNK_SIZE` | `900` |  |
| `SENTINEL_CHUNK_OVERLAP` | `120` |  |
| `SENTINEL_RETRIEVAL_TOP_K` | `6` |  |
| `SENTINEL_GROUNDING_THRESHOLD` | `0.6` | minimum citation grounding of the report; below → TPRM-AI-01 |
| `SENTINEL_SCREENING_REVIEW_THRESHOLD` | `0.88` | name similarity at/above which a hit is a potential match |
| `SENTINEL_SCREENING_MAX_RELATED_PARTIES` | `60` | cap on parents/subsidiaries/insiders screened per case |
| `SENTINEL_JWT_SECRET` | `(dev value)` | secret, ≥ 32 chars; blank falls back to a dev value that prod refuses |
| `SENTINEL_JWT_ALGORITHM` | `HS256` |  |
| `SENTINEL_JWT_TTL_MINUTES` | `30` |  |
| `SENTINEL_JWT_ISSUER` | `sentinel-dd` |  |
| `SENTINEL_RATE_LIMIT_PER_MINUTE` | `120` | per token/client, per process |
| `SENTINEL_LOGIN_RATE_LIMIT_PER_MINUTE` | `10` | per client+username, per process |
| `SENTINEL_CORS_ORIGINS` | `[]` | only needed if the UI is served from another origin |
| `SENTINEL_LOCAL_AUTH_ENABLED` | `True` | username/password login against the users table (disable behind an IdP) |
| `SENTINEL_PASSWORD_MIN_LENGTH` | `12` | user password policy |
| `SENTINEL_POLICY_VERSION` | `tprm-policy-2026.10` | recorded on every policy decision |
| `SENTINEL_SCORECARD_VERSION` | `scorecard-2026.10` | recorded on every assessment |
| `SENTINEL_AUTO_CLEAR_ENABLED` | `False` | conservative default: every case gets a human decision |
| `SENTINEL_MAX_AGENT_STEPS` | `40` | LangGraph recursion limit per case |
| `SENTINEL_RUNNER_MODE` | `background` | `background` thread pool or `inline` (CLI/tests) |
| `SENTINEL_RUNNER_WORKERS` | `2` | background worker threads per process |

## Notes

* **Thresholds are configuration, not code.** Changing `SCREENING_REVIEW_THRESHOLD`, `GROUNDING_THRESHOLD` or
  `AUTO_CLEAR_ENABLED` changes outcomes; treat such changes as policy changes (approval, version bump of
  `POLICY_VERSION`, re-run `sentinel eval`).
* **`AUTO_CLEAR_ENABLED=false`** (default) means every case ends in a human decision, even when no rule fires
  (rule TPRM-GOV-01).
* **Database URLs:** SQLAlchemy URL for data (`postgresql+psycopg://…`), plain libpq DSN for the LangGraph
  checkpointer (`postgresql://…`). Install the `postgres` extra (`pip install -e ".[postgres]"`).
