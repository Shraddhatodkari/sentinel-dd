# ADR-0004: Hybrid retrieval with claim-level grounding verification

**Status:** Accepted

## Decision
Combine dense and BM25 retrieval with Reciprocal Rank Fusion (no score calibration needed), then a lexical reranker and MMR. Retrieval is per case, over evidence collected for that case only. Every answer sentence must cite
a retrieved chunk and pass a lexical-support threshold. The
aggregate grounding score feeds the critic loop and the policy engine.

## Rationale
Due diligence relies on exact identifiers (company names, registration numbers), where BM25 does well, and on
paraphrase, where dense retrieval does well. Claim-level grounding makes hallucinations measurable and
actionable instead of anecdotal.
