# Threat Model (STRIDE-lite) & Controls

| Threat | Vector | Control (code) |
|---|---|---|
| Direct prompt injection | user question / entity field | `Guardrails.check_input` (pattern + base64 decoding), 422 + audit |
| Indirect prompt injection | instructions hidden in vendor documents | `Guardrails.sanitize_context` before the LLM; the LLM has no approval authority (ADR-0002) |
| PII leakage to LLM provider | documents with emails, cards, Aadhaar | DLP tokenisation **before** indexing/embedding (`IngestionPipeline`) |
| PII leakage in outputs | memos / answers | DLP masking on reports; output guardrails |
| Credential leakage | secrets in docs or logs | API_KEY / PRIVATE_KEY detectors, gitleaks in CI, secrets only via secret manager |
| Spoofing | forged JWT, unknown roles | HS256 with ≥ 32-byte secret, issuer/exp/jti required, revocation list, unknown-role rejection |
| Elevation of privilege | analyst approving own case | RBAC permissions + maker-checker in `ApprovalQueue.decide` |
| Repudiation | "I never approved that" | SHA-256 hash-chained audit log, `GET /audit/verify` |
| Tampering with models | swapped pickle artifact | SHA-256 integrity check on registry load |
| DoS / cost abuse | request floods, huge inputs | token-bucket rate limit, input length limits, bounded agent steps (`recursion_limit`) and reflection loops |
| Supply chain | unpinned model weights, vulnerable deps | `--revision` pinning for HF weights, pip-audit, Trivy, bandit |
| Container breakout | — | non-root, read-only root FS, dropped capabilities, seccomp, NetworkPolicy |
