# Gap analysis: v1 → v2

v1 (the first series of commits in the history) demonstrated the architecture on fictional companies. This
table records what was inspected, what was wrong for an enterprise system, and what v2 changed.

| Area | v1 state | Gap | v2 change |
|---|---|---|---|
| Data | Fictional companies (Helios, Northwind, BlueOrbit), invented documents, invented watch-list | No evidence value; cannot be validated | Real authoritative sources; fictional data removed; recorded real responses for tests ([data_sources.md](data_sources.md)) |
| Ingestion | Loader over local Markdown/CSV | No provenance, freshness, validation, idempotency | Resilient fetcher, content-addressed raw store, `source_documents`, ingestion runs, DQ checks, shrink gate, versioning, freshness SLA |
| Persistence | In-memory stores, JSON files, file audit log | State lost on restart; no transactions | SQLAlchemy + Alembic (PostgreSQL/SQLite), constraints, indexes, append-only audit table with triggers |
| Layers | Facts, model output and decisions mixed in one result | Not auditable | Separate tables for source facts, signals, assessments, policy decisions, human decisions |
| Entity resolution | Lookup in a fictional registry | — | EDGAR + GLEIF resolution with confidence and ambiguity |
| Screening | Fuzzy match vs. 20 invented entries | No real recall/FPR | Five official lists (33 649 records on 2026-10-04), aliases, identifiers, IDF-weighted matching; measured on full lists |
| Ownership/UBO | Pattern/LLM extraction from fictional text | Unverifiable on real data | Evidence-backed GLEIF/Exhibit 21/Form 3/4 graph; explicit "not a UBO register" limitation |
| Risk model | GBM trained on synthetic labels | Arbitrary probabilities | Versioned scorecard with evidence per factor; IsolationForest peer model on real SEC data ([ADR-0007](adr/0007-scorecard-instead-of-ml-classifier.md)) |
| LLM | Fake LLM in tests; generation could add facts | Hallucination risk | Extractive default (no generation); grounding enforced; usage/cost metered |
| Auth | Demo login, hard-coded users | Not enterprise | Users table, PBKDF2, lockout, JWT revocation, least-privilege roles, tenants |
| Review | Approval endpoint with requester ≠ reviewer | No tiering, no rationale requirement | Tiered maker-checker, rationale, single decision, transactional audit |
| Dashboard | None (Swagger only) | Not usable by analysts | React analyst dashboard on the real API with browser e2e test |
| Observability | Basic metrics | No source/LLM/agent visibility | Source, agent, LLM, guardrail metrics; freshness; ingestion runs; agent trace per case |
| Reliability | Retries on LLM only | Upstream failures broke cases | Retries/backoff/breakers per host, degraded coverage, checkpoint retry, start-up recovery |
| Tests | Unit tests on fictional data | Measured nothing real | Offline replay of real data, API + browser e2e, live smoke tests, CLI tests |
| Evaluation | Golden sets over fictional docs | — | Release gates on recorded real data + full-list screening evaluation ([evaluation.md](evaluation.md)) |
| Docs | Claimed "98 % coverage", "102 passed" badges | Unverified claims | Only figures from runs recorded in [evaluation.md](evaluation.md) |
| Infra | Compose with Qdrant/MLflow services the default code path did not need; no dashboard | Misleading footprint | Image with dashboard, PostgreSQL compose, migration Job, refresh CronJob |

Still open: see [limitations.md](limitations.md).
