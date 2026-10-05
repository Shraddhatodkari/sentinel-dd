# Backup and disaster recovery

## What must be protected

| Asset | Location | Why |
|---|---|---|
| Database | PostgreSQL | cases, evidence metadata, signals, assessments, decisions, reviews, users, audit chain, list versions |
| Raw source store | `$SENTINEL_DATA_DIR/raw` | bytes behind every evidence item (lineage/integrity) |
| Model registry | `$SENTINEL_DATA_DIR/models` | peer model artefacts referenced by assessments |
| Checkpoints | PostgreSQL (`SENTINEL_CHECKPOINT_URL`) or `$SENTINEL_DATA_DIR/checkpoints.sqlite` | resume cases awaiting review |
| Secrets | secret manager | JWT secret, DB credentials, LLM keys |
| Configuration | git + ConfigMap | policy versions, jurisdiction policy |

## Suggested objectives

RPO ≤ 15 min, RTO ≤ 4 h for the database; RPO ≤ 24 h for the raw store (re-fetchable from upstream for per-entity
data, but historical list versions and point-in-time evidence are not).

## Backups

* PostgreSQL: continuous WAL archiving + daily base backup (PITR), cross-region copy, encrypted; monthly restore test.
* Data volume: daily snapshot (or object-storage replication if mounted from S3/GCS via CSI), versioning enabled.
* Audit: in addition to DB backups, stream `audit_events` to WORM storage or a SIEM so a DB-level compromise cannot
  rewrite history unnoticed.

## Restore procedure

1. Restore PostgreSQL to the target point in time.
2. Restore the data volume snapshot taken at or after that time.
3. Deploy the same image version (or newer — migrations are forward-only), run `sentinel db upgrade`.
4. `sentinel audit verify` → must report `intact: true`.
5. Spot-check lineage: pick recent evidence, call `GET /api/v1/documents/{id}` → `integrity_verified: true`.
6. `sentinel sources refresh --all` to bring lists back within SLA; check `/ready`.
7. Cases that were `running` at the failure point are marked failed on start-up; retry them.

## Degraded modes

| Failure | Behaviour |
|---|---|
| Upstream source down | collectors record `partial`/`unavailable`; policy forces REVIEW; breakers stop hammering the host |
| All lists stale | screening uses last good versions; every case gets TPRM-DAT-03 |
| LLM provider down | fallback chain → extractive backend; no outage |
| Data volume unavailable | new evidence cannot be stored → cases fail and can be retried after recovery |
| Database unavailable | `/ready` 503; API returns errors; no partial writes (transactions) |
