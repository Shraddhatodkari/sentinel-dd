# ADR-0003: Pluggable infrastructure with offline defaults

**Status:** Accepted

## Decision
Every infrastructure dependency has an interface and at least two implementations: an offline/in-process default
used in tests, and a production adapter (Qdrant/pgvector, Anthropic/Bedrock/Vertex/OpenAI, Sentence-Transformers,
MLflow). These are wired in `container.py` from environment variables.

## Consequences
+ CI is hermetic, fast (~15 s) and free; no secrets are needed in CI.
+ Hyperscaler portability: the same code runs on AWS (Bedrock), GCP (Vertex) or with direct Anthropic access.
- The in-memory defaults are single-replica; production deployments must configure external stores.
