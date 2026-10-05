# Threat model

Method: STRIDE over the data-flow diagram in [architecture.md](architecture.md). Assets: case data and decisions,
audit trail integrity, credentials/tokens, LLM provider keys, reviewer identity, list versions used for decisions.

## Trust boundaries

1. Browser / MCP client ↔ API (untrusted input, authenticated by JWT)
2. API ↔ database and data volume (trusted network, credentials from secret store)
3. Service ↔ upstream data sources (untrusted content over TLS)
4. Service ↔ LLM provider (optional; data leaves the boundary)
5. Operator CLI ↔ database (privileged; runs inside the cluster/host)

## Threats and mitigations

| # | STRIDE | Threat | Mitigation | Residual risk |
|---|---|---|---|---|
| T1 | Spoofing | Credential stuffing / brute force | PBKDF2 600k, lockout, login rate limit, audit of failures | Per-process rate limit; add gateway limits / MFA via IdP |
| T2 | Spoofing | Stolen JWT replay | 30-min TTL, revocation on logout, HSTS, `no-store` | No refresh-token rotation or device binding |
| T3 | Tampering | Editing or deleting audit history | Append-only triggers, hash chain + `audit_head`, verify endpoint | A DB superuser can drop triggers; ship audit to WORM storage / SIEM |
| T4 | Tampering | Poisoned upstream data (e.g. altered list file) | TLS to official hosts only, shrink gate, version key, raw bytes hashed and retained, per-hit list version | Upstream compromise at the publisher is not detectable |
| T5 | Tampering | Model artefact substitution | SHA-256 integrity check on load; promotion requires `provenance=real` and lineage | Registry directory must be write-protected |
| T6 | Repudiation | Reviewer denies a decision | Identity, role, timestamp, rationale in `reviews` and audit; one decision per review | Shared accounts defeat this — enforce per-person accounts |
| T7 | Information disclosure | Analyst reads other analysts' or tenants' cases | Visibility filter on every query, 404 for out-of-scope ids | — |
| T8 | Information disclosure | PII or secrets sent to an LLM / shown in reports | Default `extractive` backend (no external calls), DLP masking on reports, input guardrails | When an external LLM is enabled, filing text is sent to the provider (public data; DPA still required) |
| T9 | Information disclosure | Stack traces / internals in errors | Sanitised error handler with request id | — |
| T10 | DoS | Request floods / huge bodies | Rate limit, 1 MB body limit, bounded worker pool, recursion limit | Per-process limits; no queue back-pressure across replicas |
| T11 | DoS | Upstream slowness cascades | Timeouts, breakers, per-host intervals, degraded coverage instead of failure | GDELT frequently unavailable (documented) |
| T12 | Elevation | Prompt injection in documents makes the agent approve | LLM has no approval tool or authority; deterministic policy; indirect-injection scanning; input guardrails | Novel injections may evade regexes but cannot change decisions |
| T13 | Elevation | Tool misuse via MCP | Service principal with RBAC; tool calls audited; no decision tool | MCP principal's own permissions define blast radius |
| T14 | Elevation | Self-approval | Maker-checker on creator/requester, tier-based role requirement | Collusion between two users |
| T15 | Spoofing | SSRF via user-supplied URLs | No endpoint fetches user-supplied URLs; connectors build URLs from fixed hosts | — |
| T16 | Tampering | XSS in dashboard via source content (titles, filings) | React escaping, custom Markdown renderer builds elements (no `dangerouslySetInnerHTML`), CSP without inline script | — |

## Abuse cases tested

* Self-review by the creator → 403 (API and browser e2e)
* Reviewer deciding an ESCALATE case → 403; second decision → 409
* Cross-tenant and cross-analyst case access → 404
* Revoked token reuse → 401
* Prompt-injection corpus → blocked (evaluation gate), benign corpus → allowed
* Audit row update/delete → rejected by triggers; tampering after dropping a trigger → detected by `verify` (unit test)
