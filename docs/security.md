# Security controls

| Area | Control | Where |
|---|---|---|
| Authentication | Username/password against the `users` table; PBKDF2-HMAC-SHA256 (600 000 iterations), constant-time compare, dummy hash for unknown users (timing), lockout for 15 min after 5 failures, per-client login rate limit | `security/passwords.py`, `security/users.py`, `api/main.py` |
| Tokens | HS256 JWT (issuer, expiry, `jti`), 30 min TTL by default; logout revokes the `jti` in `revoked_tokens` (checked on every request); `local_auth_enabled=false` disables password login when an IdP issues tokens | `security/auth.py` |
| Authorisation | Least-privilege RBAC: analyst (create/read own cases), reviewer (read all, decide REVIEW tier), senior_reviewer (+ ESCALATE tier), auditor (read all, audit, system status), admin (users, source refresh, read all cases, audit, system status) — admins cannot create or decide cases. Model training and SFT export are operator CLI actions (no API endpoint), so `model:train` is reserved for a future endpoint | `security/auth.py` |
| Case access | Tenant isolation on every query; analysts see only their own cases; 404 (not 403) for cases outside scope | `agents/service.py` |
| Maker-checker | The case creator / review requester cannot decide the review; ESCALATE needs `review:decide_escalated`; rationale ≥ 10 chars; one decision per review (409 on repeat); decision, case status and audit record in one transaction | `agents/service.py` |
| Input validation | Strict pydantic models (`extra="forbid"`), length/pattern limits, 1 MB body limit, prompt-injection guardrails on case queries and questions | `api/schemas.py`, `security/guardrails.py` |
| Output validation | Report grounding check; output guardrails (citations required; over-claiming, legal-advice and regulatory-determination language blocked); DLP masking of the report | `agents/nodes.py`, `security/dlp.py` |
| DLP | Detectors for payment cards (Luhn), IBAN (mod 97), Aadhaar (Verhoeff), PAN, SSN, e-mail, phone, API keys/secrets; mask / hash / tokenise strategies | `security/dlp.py` |
| Indirect prompt injection | Retrieved evidence is scanned; flagged chunks are excluded from LLM context and counted (`indirect_injection_blocked`) | `rag/qa.py`, `security/guardrails.py` |
| Tool authorisation | Every tool (Claude agent, MCP) runs as a principal and checks RBAC; no tool records review decisions; MCP server runs as a dedicated service user | `agents/tools.py`, `mcp_server/server.py` |
| Transport & headers | HSTS, CSP (`default-src 'self'`, no inline script; relaxed only on `/api/docs`), X-Frame-Options DENY, nosniff, Referrer-Policy, Permissions-Policy, COOP, `no-store` on API responses | `api/security.py` |
| Rate limiting | Token bucket per token/client on `/api/*` (default 120/min) and on login (10/min) | `api/security.py` |
| Errors | Uniform JSON errors with request id; unhandled exceptions return `internal_error` without stack traces; validation errors return field locations only | `api/main.py`, `api/security.py` |
| Audit | Hash-chained, append-only `audit_events` (DB triggers block UPDATE/DELETE); login success/failure, logout (token revocation), CLI token issue, case created/started/retried/failed/finalised/blocked, review approved/rejected, user creation, source refresh requested (API) and every refresh outcome (`source.refresh_succeeded` / `_failed`, from API, CLI or CronJob), ad-hoc screening, agent/MCP tool calls, audit verification; `GET /api/v1/audit/verify` recomputes the chain | `security/audit.py`, migrations |
| Secrets | `SecretStr` settings, `.env` git-ignored, `.env.example` without values, prod start-up validation, build-time CA via BuildKit secret (not in image) | `config.py`, `Dockerfile` |
| Supply chain | `bandit`, `pip-audit`, `npm audit`, gitleaks, Trivy in CI; pinned lockfile for the dashboard | `.github/workflows/ci.yml` |
| Runtime | Non-root UID 10001, read-only root FS, dropped capabilities, seccomp RuntimeDefault, NetworkPolicy | `Dockerfile`, `infra/` |

## Reporting

See [SECURITY.md](../SECURITY.md). Threats and residual risks: [threat_model.md](threat_model.md).
