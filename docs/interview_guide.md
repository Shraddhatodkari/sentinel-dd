# Walkthrough & Interview Guide

Use this guide to learn the codebase well enough to defend every design decision. Interviewers will probe
*why* things were built this way, so read the code behind each answer and be ready to change it live.

## 60-second pitch

"Sentinel is a governed multi-agent platform for vendor due diligence. A LangGraph workflow fans out to a RAG
researcher, a GraphRAG ownership analyst and a sanctions screener in parallel. A calibrated ML model then scores
the risk with explanations, a writer drafts a cited memo, and a critic loops back if grounding is weak. A
declarative policy engine, not the LLM, decides whether the case can be auto-approved or must pause for a human
reviewer with maker-checker controls. Everything is DLP-protected, RBAC-secured, hash-chain audited, exposed over
REST and MCP, and covered by 102 offline tests plus eval gates in CI."

## Suggested learning path (2–3 weeks)

1. Run `make check` and `make demo`. Read `agents/workflow.py`, then step through `agents/nodes.py` in a debugger.
2. RAG: `rag/retriever.py` → `rag/qa.py` → `rag/grounding.py`. Change `candidate_k`, MMR `lam` and RRF `k`, and
   watch `scripts/evaluate.py`.
3. GraphRAG: `graphrag/extractor.py` → `graphrag/graph.py`. Add a new relation pattern (e.g. "guarantor of") with a test.
4. ML: `ml/features.py` → `ml/models.py` → `ml/train.py`. Add a feature end-to-end (generator, scoring, test).
5. Security: `security/dlp.py` (add a detector, e.g. GSTIN), `security/guardrails.py`, `api/security.py`.
6. Swap in a real Claude key and compare grounding scores with the `FakeLLM` run.

## Likely questions (and where the answer lives)

| Question | Talking points |
|---|---|
| Why LangGraph over CrewAI/AutoGen? | Deterministic graph, typed state, reducers, durable `interrupt` for HITL, checkpoint replay (ADR-0001). CrewAI/AutoGen adapters reuse the same tools. |
| How do you stop hallucinations? | Grounded prompts, `[doc#n]` citations, claim-level grounding score, abstention, critic loop, policy forces HITL on low grounding. |
| How do you defend against prompt injection? | Input scoring (incl. base64), context sanitisation for *indirect* injection, and most importantly the LLM has no authority to approve (ADR-0002). The demo shows an injected vendor document being neutralised. |
| Why hybrid retrieval? | Exact identifiers (BM25) plus paraphrase (dense); RRF avoids score calibration; MMR reduces redundancy. |
| Why GraphRAG? | Multi-hop ownership ("sanctioned UBO three layers up") is a graph problem; effective ownership multiplies stakes along paths. |
| How is the ML governed? | Quality gates, champion/challenger, SHA-256 integrity, model card, PSI drift, attributions in every memo. |
| What is LoRA, concretely? | `W + (α/r)·B·A` with B initialised to zero; only A and B train; merge for zero-latency inference. See `finetune/lora.py` and the merge-is-lossless test. |
| How would you scale it? | Qdrant/pgvector HNSW, Postgres checkpointer, async workers per case, Neo4j for large graphs, LLM router fallback (architecture.md). |
| What is synthetic or simplified? | Training data, the offline LLM/embedder defaults and single-process stores. Say this openly and explain the production path. |

## Presenting the project honestly

Describe this as a **personal / portfolio project** you built to deepen hands-on skills in agentic AI, RAG and
MLOps. Do not present it as client or employer work. Its value comes from your ability to explain and extend it,
not from the dates on the commits.
