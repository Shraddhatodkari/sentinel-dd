# Limitations and known risks

This page lists what Sentinel does **not** do, or does only partially, as of v2.0.0. It is part of the product
documentation, not a disclaimer to be skipped.

## Scope and data

1. **Not a regulatory determination or a certified product.** Outputs support a human decision. Sentinel has not been
   validated by a regulator, audited by a third party, or deployed at a regulated institution.
2. **No beneficial-ownership (UBO) register.** Ownership comes from GLEIF accounting-consolidation data, SEC Exhibit 21
   and Form 3/4. Natural-person owners of private or non-US companies are generally not visible. National UBO
   registers (e.g. UK PSC via Companies House API) are not integrated yet.
3. **PEP coverage is narrow by default** (US Congress). Global PEP data requires a licensed source; the OpenSanctions
   adapter is present but disabled because its data licence is non-commercial.
4. **Adverse media is thin.** DOJ press releases (US federal enforcement) work reliably; GDELT is frequently throttled
   or unreachable from shared IPs and was unavailable for most cases in the release runs. An empty GDELT result (`{}`) is
   indistinguishable from some GDELT-side failures, and the default window is one week (see
   [data_sources.md](data_sources.md)). A commercial news/risk data provider is needed for production-grade
   adverse-media coverage. Only English sources are classified.
5. **SEC-centric financials.** Filings text, insiders and XBRL financials exist only for SEC registrants.
6. **Screening method.** Name-based fuzzy matching with alias and identifier support. It does not generate
   transliteration variants, and does not use date of birth, nationality or addresses to discount person matches. The
   measured false-positive rate on real US registrant names is 0.9 % against the full lists (see
   [evaluation.md](evaluation.md)); exact name collisions (e.g. a US company and a listed entity with the same
   distinctive name) are surfaced as potential matches by design.
7. **Scorecard weights are expert-set, not fitted.** There is no public labelled dataset of third-party-risk outcomes.
   Calibrate against your own decisions before relying on band thresholds.
8. **Jurisdiction tiers are a default policy** derived from FATF statements and broad sanctions programmes at the
   time of writing; they must be reviewed by Compliance and kept current.

## Engineering

9. **Case runner is in-process.** Background investigations run in a thread pool inside the API process. Multiple
   replicas work (shared PostgreSQL + checkpoints), but there is no distributed queue: a replica crash fails its
   running cases (they are recoverable via retry), and load is not balanced across replicas. A queue-based worker
   (e.g. Celery/RQ/Arq or a managed queue) is the next step for large volumes.
10. **Rate limiting is per process** (token bucket in memory). Use an API gateway/WAF for global limits.
11. **Authentication is local** (users table + JWT). There is no OIDC/SAML integration or MFA yet; behind an IdP, set
    `SENTINEL_LOCAL_AUTH_ENABLED=false` and issue tokens externally (integration code not included).
12. **Single-tenant UI.** Tenancy is enforced in the API and data model, but there is no tenant administration UI.
13. **Kubernetes manifests are reference manifests**, not a Helm chart, and were not applied to a live cluster for this
    release. The Docker image and Compose stack were built and run.
14. **Dependency audit scope.** `pip-audit` results depend on the environment it runs in; the CI job audits the
    project's installed dependency set. See [evaluation.md](evaluation.md) for the result of this release's run.
15. **Test data recency.** The offline suite replays responses recorded on 2026-10-04. Upstream formats can change;
    the weekly live smoke job (`pytest -m live`) is the guard.
16. **Fine-tuning module** (`sentinel.finetune`) is a research module (LoRA mechanics and an SFT dataset builder from
    approved cases). It is not used in the decision path and no fine-tuned model ships.
17. **Peer model** compares across all industries; banks, insurers and holding companies look structurally unusual.
    It carries at most 4 of 100 points.

## Operational risks

| Risk | Mitigation in place | Owner action |
|---|---|---|
| Upstream format change breaks a list parser | run fails visibly, current version kept, SLA → stale → reviews | monitor freshness; fix parser |
| Analyst over-reliance on the score | auto-clear off, rationale required, layers shown separately | training; QA sampling of decisions |
| False negatives in screening | identifier matching, aliases, perturbation-tested recall | periodic full-list evaluation (`sentinel eval --screening-live`) |
| DB superuser alters audit | triggers + hash chain detect | ship audit to WORM/SIEM |
| LLM enabled without DPA | default is extractive (no external calls) | legal review before enabling a provider |
