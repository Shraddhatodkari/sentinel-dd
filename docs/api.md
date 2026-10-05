# REST API (`/api/v1`)

Interactive reference: `GET /api/docs` (Swagger UI) and `GET /api/openapi.json`. All `/api/v1` endpoints except
login require `Authorization: Bearer <JWT>`. Errors are JSON: `{"error": …, "detail": …, "request_id": …}`; every
response carries `X-Request-ID` (send your own to correlate).

## Endpoints

| Method & path | Permission | Purpose |
|---|---|---|
| `GET /health` | — | liveness (`{"status":"ok","version":…}`) |
| `GET /ready` | — | readiness: database reachable, schema at Alembic head, screening lists loaded (503 otherwise) |
| `GET /metrics` | — | Prometheus exposition (restrict at the ingress) |
| `POST /api/v1/auth/login` | — | `{username, password}` → `{access_token, expires_at, username, display_name, roles, permissions, tenant}` |
| `POST /api/v1/auth/logout` | any | revokes the current token (204) |
| `GET /api/v1/auth/me` | any | current principal |
| `POST /api/v1/cases` | `case:create` | `{query, cik?, lei?, ticker?, question?}`; header `Idempotency-Key` recommended; 202 with case summary |
| `GET /api/v1/cases` | `case:read_own` / `case:read_all` | filters `status`, `band`, `q`, `limit`, `offset` → `{total, items}` |
| `GET /api/v1/cases/{id}` | read | full case: summary, resolution, coverage, events (agent trace), evidence, signals, assessment, policy, review, report, research, graph, LLM usage |
| `POST /api/v1/cases/{id}/retry` | `case:create` or `source:refresh` | retry a failed case from its checkpoint |
| `POST /api/v1/cases/{id}/decision` | `review:decide` (+ `review:decide_escalated` for ESCALATE) | `{approve: bool, comment: str (≥10)}`; 403 for the case creator, 409 if already decided |
| `GET /api/v1/reviews?status=pending\|approved\|rejected` | `review:decide` or `case:read_all` | review queue with `can_decide` per item |
| `GET /api/v1/overview` | any | counts by status/band/outcome, pending reviews, recent cases, source freshness (tenant-scoped) |
| `POST /api/v1/screening` | `search` | ad-hoc screening `{name, entity_type?}` against the current lists (audited) |
| `GET /api/v1/sources` | any | per-source freshness, SLA, records, last run |
| `POST /api/v1/sources/{id}/refresh` | `source:refresh` | start a watch-list refresh (202) |
| `GET /api/v1/sources/runs` | `system:read` or `source:refresh` | ingestion runs with data-quality details |
| `GET /api/v1/documents/{id}` | read | source document lineage: URL, retrieval time, SHA-256, size, status, `integrity_verified` |
| `GET /api/v1/audit` | `audit:read` | audit records (`resource_id`, `limit`, `before` for paging) |
| `GET /api/v1/audit/verify` | `audit:read` | recompute the hash chain → `{intact, records, first_bad_seq, head_matches}` |
| `GET /api/v1/governance` | any | policy/scorecard/peer-model versions, thresholds, jurisdiction policy, LLM, agent contracts, source catalogue |
| `GET /api/v1/system/status` | `system:read` | version, DB, schema revision, list sizes, circuit breakers, LLM usage, runner mode, audit size |
| `GET/POST /api/v1/users` | `user:manage` | list / create users in the admin's tenant |
| `GET /`, `/cases/…`, … | — | analyst dashboard (SPA) when built |

## Example

```bash
TOKEN=$(curl -s localhost:8000/api/v1/auth/login -H 'content-type: application/json' \
  -d '{"username":"alice","password":"…"}' | jq -r .access_token)

curl -s localhost:8000/api/v1/cases -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -H 'Idempotency-Key: 6f1c…' -d '{"query":"The Boeing Company"}'
# → 202 {"case_id":"case-97e9aad67149","status":"queued",…}

curl -s localhost:8000/api/v1/cases/case-97e9aad67149 -H "Authorization: Bearer $TOKEN" | jq '{status, risk_band, risk_score, policy_outcome}'
```

## Status codes

| Code | Meaning |
|---|---|
| 401 | missing/invalid/expired/revoked token, wrong credentials, locked account |
| 403 | permission missing, maker-checker violation, tier requires senior reviewer |
| 404 | not found **or** not visible to you (no existence oracle) |
| 409 | review already decided / case not in a retryable state |
| 413 | body > 1 MB |
| 422 | validation error or guardrail violation (`{"error":"guardrail_violation","violations":[…]}`) |
| 429 | rate limited (`Retry-After`) |

## MCP

```bash
sentinel users create --username mcp-svc --roles analyst          # dedicated service account, minimum roles
export SENTINEL_MCP_TOKEN=$(sentinel users token --username mcp-svc --ttl-minutes 60 | jq -r .token)
sentinel-mcp --transport stdio                                     # or sse / streamable-http
```

Tools: `search_case_evidence`, `screen_name`, `get_case`, `start_investigation`, `list_review_queue`; resources:
source freshness and the active policy. The server acts as the token's principal; every tool call is authorised
against its RBAC permissions and audited (`mcp.tool_call`). There is no tool for review decisions.
