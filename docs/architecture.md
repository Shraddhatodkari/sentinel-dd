# Architecture

## Design principles

1. **Ports and adapters.** Every external dependency (LLM, embeddings, vector DB, model registry) sits behind an
   interface. `container.py` is the single composition root, so swapping Claude for Bedrock, or the in-memory store
   for Qdrant, is a configuration change.
2. **Deterministic core, probabilistic edges.** Numbers (revenue, leverage, ownership %) come from structured
   sources and graph analytics, not from LLM free text. LLMs plan, extract and summarise, and every summary is
   checked for grounding.
3. **Governance as code.** Policy decisions are made by a declarative engine, not by the LLM. The LLM can never
   approve anything. High-impact outcomes always go through a human, and every step is written to a
   tamper-evident log.
4. **Offline-first testability.** A deterministic `FakeLLM` and a hashing embedder make all 102 tests reproducible
   in CI without network access or credentials.

## Request lifecycle (`POST /cases`)

1. `SecurityMiddleware` assigns a request ID, applies the rate limit and adds security headers. `require()`
   verifies the JWT and the permission.
2. `CaseService.start` runs input guardrails (injection score, DLP). Blocked requests are audited and return 422.
3. LangGraph runs the graph with `thread_id = case_id`:
   * **planner**: LLM structured output (`ResearchPlan`) with a safe default plan. Long-term memory supplies
     prior cases for the same entity.
   * **researcher**, **graph_analyst** and **screener** run **in parallel** in one superstep. Their `trace` and
     `errors` lists merge through `operator.add` reducers.
   * **risk_assessor** (barrier join): builds features from structured data, the graph and NLP adverse-media
     hits, then gets a calibrated probability, attributions, anomaly score and segment.
   * **report_writer**: grounded executive summary plus templated sections, then output guardrails and DLP.
   * **critic**: if grounding is weak, it generates follow-up questions and loops through **research_refine**,
     at most `max_iterations` times.
   * **compliance**: `PolicyEngine` returns ALLOW, REQUIRE_APPROVAL or DENY, and files an `ApprovalRequest`
     when approval is needed.
   * **human_review**: `interrupt()` persists the checkpoint and returns control. `POST
     /approvals/{id}/decision` calls `Command(resume=…)`.
   * **finalize**: writes long-term memory and the audit record, and closes the case.

## Retrieval design

```
query ─► analyze_query (entity/year filters, doc-type hints)
      ├─► dense (vector store, filtered) ─┐
      └─► sparse (BM25, filtered) ────────┴─► RRF ─► rerank (+doc-type boost) ─► MMR ─► sanitize ─► LLM ─► grounding check
```

* If the filters remove every candidate, retrieval falls back to an unfiltered search.
* Context is capped by a character budget. Each line is tagged `[doc_id#n]`, which is the citation key.
* `GroundingChecker` splits the answer into claims. A claim counts as supported only if it cites a retrieved
  chunk **and** at least 60% of its content tokens appear in that chunk.

## GraphRAG

* Extraction runs per sentence with high-precision patterns (ownership %, subsidiaries, officers, incorporation,
  sanctions designations, PEP status, partnerships). `LLMExtractor` adds open-domain triples under a strict
  schema.
* Entity resolution: suffix/punctuation normalisation (`Pvt. Ltd.` ≡ `Private Limited`) plus fuzzy matching.
* Analytics: multiplicative effective ownership gives the UBOs (≥ 25%, with a cycle guard). k-hop exposure gives
  explainable paths. The offshore-layer count, communities and summaries supply global context.

## Scaling notes

| Concern | Single node (default) | Production |
|---|---|---|
| Vector search | exact NumPy cosine | Qdrant HNSW / pgvector HNSW |
| Graph | NetworkX in-process | Neo4j / Neptune (same `KnowledgeGraph` API) |
| Checkpoints | `InMemorySaver` | `PostgresSaver` (multi-replica resume) |
| Approvals / audit | in-process + JSONL | Postgres + WORM object storage (S3 Object Lock) |
| LLM | single provider | `LLMRouter` with Bedrock/Vertex fallback and a circuit breaker |
| Throughput | sync | async workers / queue (Celery, SQS) per case |
