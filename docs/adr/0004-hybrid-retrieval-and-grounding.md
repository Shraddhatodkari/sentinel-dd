# ADR-0004: Hybrid retrieval with claim-level grounding verification

**Status:** Accepted

## Decision
Combine dense and BM25 retrieval with Reciprocal Rank Fusion (no score calibration needed), then rerank and
apply MMR. Every generated sentence must cite a retrieved chunk and pass a lexical-entailment threshold. The
aggregate grounding score feeds the critic loop and the policy engine.

## Rationale
Due diligence relies on exact identifiers (company names, registration numbers), where BM25 does well, and on
paraphrase, where dense retrieval does well. Claim-level grounding makes hallucinations measurable and
actionable instead of anecdotal.
