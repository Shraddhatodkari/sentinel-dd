# ADR-0003: Pluggable providers, relational system of record

**Status:** Accepted (revised in v2)

## Decision
LLM providers (extractive default, Anthropic, OpenAI, Bedrock, Vertex) and embedding backends are interfaces wired
in `container.py` from configuration. Persistent state is **not** pluggable: it lives in a relational database
(PostgreSQL in production, SQLite for development and tests) managed by Alembic migrations. The v1 in-memory vector
stores and graph were removed; per-case retrieval indexes are rebuilt from persisted chunks.

## Consequences
+ CI is hermetic: tests replay recorded real responses, use SQLite and the extractive LLM — no secrets needed.
+ One transactional store for cases, evidence, decisions and audit.
- External vector databases are not used; per-case indexes are small, so this is acceptable at current scale.
