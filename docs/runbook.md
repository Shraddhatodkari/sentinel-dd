# Operations runbook

## Health signals

| Check | Meaning | Action when failing |
|---|---|---|
| `GET /health` | process alive | restart (orchestrator does this) |
| `GET /ready` (503 when not ready) | DB reachable, schema at Alembic head, screening lists loaded | run `sentinel db upgrade`; run `sentinel sources refresh --all`; check DB connectivity |
| Dashboard → Data sources / `GET /api/v1/sources` | per-source freshness vs. SLA, last run | see "Watch-list refresh failed" |
| `GET /api/v1/system/status` | circuit breakers, list sizes, LLM usage, schema revision | see below |

## Metrics and suggested alerts (Prometheus names)

| Metric | Suggested alert |
|---|---|
| `http_requests_total{status=~"5.."}` | > 1 % of requests for 10 min |
| `source_requests_total{outcome="exhausted"\|"circuit_open"}` by host | sustained for 30 min → upstream outage; cases will show degraded coverage |
| `source_retries_total` by host | spike → upstream throttling (GDELT/SEC); check fair-access limits and User-Agent |
| `cases_failed_total` | any increase → inspect `case_events` errors, retry |
| `agent_node_seconds` | p95 per node regression |
| `llm_errors_total`, `llm_fallbacks_total` | fallback ratio > 5 % for 10 min → provider incident |
| `llm_cost_usd_total` | budget threshold per day |
| `guardrail_blocks_total`, `indirect_injection_blocked_total` | sudden spike → possible abuse; review audit |
| `rag_grounding_score` (mean = sum / count) | < 0.8 for 1 h → retrieval/LLM regression |
| `authz_denied_total`, `auth_failures_total`, `rate_limited_total` | spikes → credential attack or misconfigured client |
| Source freshness (`stale=true` in `/api/v1/sources`) | any sanctions list stale → page compliance operations |

Logs are JSON on stdout with `request_id` (runner events such as `case_failed` carry `case_id`; per-node execution is in `case_events`); correlate with the `X-Request-ID`
response header.

## Procedures

**Watch-list refresh failed / list stale.** Dashboard → Data sources → last run error. Typical causes: upstream URL
or format change (parser error), network/egress block, shrink gate ("record count fell … refusing to replace").
The current version stays active, so screening continues on the last good list and new cases get TPRM-DAT-03
(review) once the SLA is exceeded. Fix the cause, then `sentinel sources refresh --source <id>` (or the dashboard
button as admin). If the shrink gate fired, compare the new file against the publisher's notice before forcing
anything.

**Case failed.** Case page → Agent trace shows the failing node and error. Fix the cause (e.g. upstream outage) and
**Retry investigation** (`POST /api/v1/cases/{id}/retry`). Cases interrupted by a restart are marked failed with
"interrupted … retry available".

**Upstream source throttling (SEC 403/429, GDELT 429).** Confirm `SENTINEL_SEC_USER_AGENT` contains a real contact;
do not lower `http_rate_limits_enabled`. GDELT throttling is expected from shared IPs; cases record `partial` media
coverage.

**LLM provider outage.** Fallback chain handles it automatically (ending with the extractive backend). To pin a
provider: set `SENTINEL_LLM_PROVIDER` and roll the deployment.

**Rotate the JWT secret.** Update the secret store, roll the deployment; existing tokens become invalid and users
sign in again.

**Account locked.** Lock clears after 15 minutes. Repeated lockouts across accounts → treat as credential attack.

**Peer model retrain / drift.** `sentinel ml drift --year <latest complete fiscal year>`; exit code 2 → open a
model-risk ticket and run `sentinel ml train --year <year>`. Promotion happens only if the gates pass.

**Audit integrity check.** Dashboard → Audit trail → Verify chain integrity (or `sentinel audit verify`).
`intact=false` → security incident: preserve the database, identify `first_bad_seq`, compare with backups/SIEM copy.

**Review backlog.** Review queue (pending) sorted oldest first; escalate items older than your SLA. ESCALATE items
need a senior reviewer.

**Jurisdiction policy update** (after a FATF plenary or sanctions change): update the JSON at
`SENTINEL_JURISDICTION_POLICY_PATH` with a new `version`, get Compliance sign-off, roll the deployment; the version is
recorded on every new assessment.
