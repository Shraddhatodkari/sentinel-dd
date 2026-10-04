# Operations Runbook

## Health
* `GET /health`: liveness. `GET /ready`: readiness (index non-empty, model loaded). `GET /metrics`: Prometheus.

## Key metrics & alerts
| Metric | Alert |
|---|---|
| `llm_errors_total`, `llm_fallbacks_total` | fallback rate > 5% for 10 min → provider incident |
| `guardrail_blocks_total`, `indirect_injection_blocked_total` | sudden spike → possible attack; review audit |
| `rag_grounding_score` (avg) | < 0.8 for 1 h → retrieval/LLM regression |
| `http_requests_total{status="5xx"}` | > 1% → page on-call |
| `rate_limited_total` | sustained → client misbehaving or capacity |
| `cases_closed_total{status}` | auto-approval ratio shifts > 20% week over week → investigate drift |

## Common procedures
* **Rotate JWT secret:** update the secret manager, roll the deployment. Old tokens become invalid (users log in again).
* **LLM provider outage:** confirm the circuit breaker opened (`llm_fallbacks_total`). Optionally pin
  `SENTINEL_LLM_PROVIDER=bedrock`.
* **Model retrain:** `python -m sentinel.ml.train --model-dir s3://…`. Promotion is automatic only if the gates
  pass and the challenger beats the champion.
* **Drift alert:** `python scripts/drift_check.py`. Exit code 2 means a retrain is recommended. Open a model-risk
  ticket.
* **Audit integrity check:** `GET /audit/verify` (auditor role). `intact=false` → security incident procedure.
* **Stuck approvals:** `GET /approvals` (reviewer). Escalate any approval older than the SLA (for example 2 business days).
