# ADR-0005: GraphRAG for ownership & exposure analysis

**Status:** Accepted

## Decision
Build a knowledge graph during ingestion (pattern + LLM extraction with entity resolution) and answer ownership
questions with graph algorithms: multiplicative effective ownership, shortest-path exposure and community
detection. Text retrieval alone is not used for these.

## Rationale
"Is there a sanctioned UBO three layers up?" is a multi-hop question that vector search cannot answer reliably.
Graph analytics give exact, explainable paths, which are quoted in the memo with their source citations.
