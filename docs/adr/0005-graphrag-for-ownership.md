# ADR-0005: Evidence-backed relationship graph (GraphRAG)

**Status:** Accepted (revised in v2)

## Decision
The case graph is built **only** from persisted relationships that carry an evidence id: GLEIF Level 2
consolidation, SEC Exhibit 21 subsidiaries and Form 3/4 insiders. No relation is extracted by an LLM from free text.
Screening hits flag nodes; graph analytics produce exposure paths (≤ 3 hops) where every edge cites its evidence,
and linearised graph facts with citations ground the narrative.

## Rationale
v1 extracted ownership from fictional documents with patterns and an LLM. With real data, extraction errors would be
unverifiable assertions about real companies. Structured registry and filing data give exact, citable edges.

## Consequences
- The graph is not a beneficial-ownership register (see limitations); natural-person owners of private companies are
  usually absent.
