# ADR-0006: Real authoritative data with record/replay testing

**Status:** Accepted

## Context
v1 ran on fictional companies and documents. A due-diligence system evaluated on invented data says nothing about
its behaviour on real entities, and fictional evidence must never appear in a real case.

## Decision
* Only authoritative or clearly licensed public sources (OFAC, UN, UK FCDO, EU, US Congress, SEC EDGAR, GLEIF, DOJ,
  GDELT). Sources whose licence restricts commercial use are present as disabled adapters.
* Every response is stored content-addressed with lineage metadata; evidence references it.
* Offline tests replay recorded real responses through an httpx transport. Large lists are represented by documented
  excerpts (unmodified rows, selection rule and full-file hash recorded). Unrecorded requests return 503 (treated as
  an outage), never synthetic content.
* Full-list metrics are produced from live runs and stored under `docs/evidence/`.

## Consequences
+ Tests exercise real formats and real edge cases (Cyrillic legal names, missing XBRL concepts, throttling).
- Upstream changes can make recordings stale; a weekly live smoke job detects this.
