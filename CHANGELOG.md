# Changelog

## [2.0.0] - 2026-10-05

Rebuilt on real authoritative data. Breaking changes to the API (`/api/v1`), storage and configuration.

### Added
- Connectors for OFAC SDN, UN SC, UK Sanctions List, EU FSF, US Congress (PEP), SEC EDGAR (index, submissions,
  XBRL company facts and frames, 10-K, Exhibit 21, Forms 3/4), GLEIF Level 1/2, DOJ press releases and GDELT.
- Resilient HTTP client, lineage fetcher, content-addressed raw store, reference-list ingestion with data-quality
  gates, versioning and freshness SLAs.
- Relational persistence (SQLAlchemy 2.0, Alembic, PostgreSQL/SQLite) with append-only, hash-chained audit.
- Entity resolution, evidence/signal model, explainable versioned scorecard, deterministic TPRM policy rules,
  IsolationForest peer model on SEC frames, adverse-media subject-role classifier.
- Tiered maker-checker review workflow; users, PBKDF2 passwords, lockout, JWT revocation, tenants.
- React analyst dashboard; Playwright browser e2e test.
- CLI (`sentinel db|users|sources|ml|investigate|audit|eval`), live smoke tests, full-list screening evaluation.
- Container image with dashboard, PostgreSQL compose stack, Kubernetes migration Job and refresh CronJob.

### Changed
- Default LLM backend is extractive (no external calls); generative providers are optional and grounding-checked.
- Auto-clear disabled by default.

### Removed
- Fictional corpus, entity registry and watch-list; synthetic-label risk classifier; in-memory vector stores and
  graph; v1 demo scripts.

## [1.0.0] - 2026-10-04
Initial architecture prototype on fictional data.
