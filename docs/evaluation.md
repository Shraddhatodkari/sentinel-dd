# Evaluation methodology and results

All figures on this page come from runs whose raw output is stored in `docs/evidence/`. Re-run the commands to
reproduce them; live figures change as the upstream lists change.

## 1. Offline release gates (`sentinel eval`)

Runs against recorded real source data (`tests/fixtures/http`, recorded 2026-10-04, plus one real GDELT `artlist`
recorded 2026-10-05): documented excerpts of the five official lists (2 773 records) and recorded SEC/GLEIF/DOJ
responses for Apple, Boeing and Sberbank. GDELT has no recording for the evaluation queries, so it is reported as
unavailable in replayed cases.

| Metric | Definition | Gate | Result (2026-10-05) |
|---|---|---|---|
| `screening_recall` | 228 sanctions-list names sampled (seed 7), each perturbed (case, word rotation, punctuation removal, title-case); hit = the same list entry returned above threshold | ≥ 0.95 | **1.000** |
| `screening_fpr_max` | 1 000 real SEC registrant names (company index); share with any sanctions potential match | ≤ 0.01 | **0.000** (excerpts only — see §2 for full lists) |
| `media_role_accuracy` | subject/victim/mentioned on 24 hand-labelled real DOJ titles (`data/eval/media_roles.json`) | ≥ 0.85 | **0.958** |
| `retrieval_recall_at_5` | 6 questions over the Apple case; target 10-K section/evidence kind in top 5 | ≥ 0.80 | **1.000** |
| `routing_accuracy` | Apple → LOW/REVIEW (TPRM-GOV-01); Boeing → MEDIUM/REVIEW (TPRM-RSK-02, TPRM-PEP-01); Sberbank → HIGH/ESCALATE (TPRM-SAN-01) | = 1.0 | **1.000** |
| `mean_grounding` | mean citation-grounding of non-abstaining research answers | ≥ 0.80 | **1.000** |
| `injection_block_rate` | 10 adversarial inputs (direct, role-play, tag spoofing, base64, exfiltration, governance bypass) | ≥ 0.90 | **1.000** |
| `benign_pass_rate` | 6 benign entity names / questions | = 1.0 | **1.000** |

Raw output: `docs/evidence/offline_eval.json`. Caveats: the routing set has three entities and is a regression
check, not a measure of accuracy; grounding uses the extractive backend (it cannot invent text, so 1.0 is expected —
the gate matters when a generative provider is enabled); the injection corpus is small and regex-based detection
can be evaded (decisions are still not delegated to the model).

## 2. Screening on the full official lists (`sentinel eval --screening-live`)

Run inside the production container against PostgreSQL after a live download on 2026-10-05: OFAC SDN 19 488,
UN 1 011, UK 6 370, EU 6 241 sanctions records and 539 US Congress members (33 649 total).

| Metric | Result |
|---|---|
| Recall on 224 perturbed real list names | **1.000** (224 / 224) |
| Share of 1 000 real SEC registrant names with a potential match | **0.009** (9 / 1 000) |

The nine potential matches are mostly exact collisions of distinctive names (`SUN`, `NOVA`, `Tidewater`, `VASO`) and
partial overlaps (`Iron Mountain Inc` ~ `Iron Mountain Enterprises Limited`, `Data Storage Corp` ~ `LLC Data Storage
Center`). Before the IDF-weighted organisation matching introduced in this release, the same run gave 0.025
(25 / 1 000) with recall 0.982. The negatives are presumed, not verified, non-designated. Raw output:
`docs/evidence/screening_full_lists_2026-10-05.json`.

## 3. Peer model training (live)

`sentinel ml train --year 2024` on live SEC XBRL frames: 3 722 filers, anomaly rate 0.050, seed agreement 0.990,
all gates passed, promoted as `peer_anomaly:v1`. Raw output: `docs/evidence/live_peer_training_2026-10-05.json`.

## 4. Automated tests

| Suite | Content |
|---|---|
| unit | connectors and parsers (recorded real responses), HTTP resilience and insert races, raw store integrity, screening, scorecard, policy, media classifier, peer model + registry + drift, RAG/grounding, LLM metering/routing/fallback chain, DLP/guardrails, auth/passwords/users, audit chain and triggers, migrations, deployment manifests / config docs / version consistency, LoRA mechanics on real labelled titles |
| integration | full investigations (replay), agent contracts, MCP tools, governance and maker-checker, CLI, SFT export from human-approved cases |
| e2e (API) | auth, lockout, RBAC matrix, idempotency, full lifecycle, tenant isolation, security headers, settings applied when started via `--factory` |
| e2e (browser) | Playwright/Chromium against the built dashboard: two investigations, tabs, lineage check, reviewer approval, senior-reviewer rejection, audit verification, governance and sources pages |
| live (opt-in) | official list downloads, EDGAR+GLEIF resolution, DOJ search |

Result of the final run for this release: **114 passed, 0 failed, 0 skipped** (3 live tests deselected by default;
browser e2e included; one third-party deprecation warning, no resource warnings). Coverage: **94.83 %** line
coverage of `src/sentinel` (excluding the optional Bedrock/Vertex clients, CrewAI/AutoGen adapters and the GPU
fine-tuning script, as configured in `pyproject.toml`; gate 80 %). Raw output:
`docs/evidence/pytest_final_2026-10-05.txt`.
Live suite (`pytest -m live`): **3 passed** (2026-10-05).

## 5. Live end-to-end verification

Docker image built from this repository, Compose stack with PostgreSQL 16, on 2026-10-05:

1. Migrations applied on start (`0002_revoked_tokens` = head); `/ready` false until lists were loaded.
2. `sentinel sources refresh --all`: five lists loaded (counts above), 0 rejected; a second refresh of OFAC returned
   `unchanged=true` with no new version (idempotency).
3. Peer model trained on live SEC frames (§3).
4. Five investigations through the API by an analyst (raw: `docs/evidence/live_e2e_2026-10-05.json`):

| Query | Resolved | Band / score | Policy | Rules | Evidence | Notes |
|---|---|---|---|---|---|---|
| Apple Inc. | Apple Inc. (CIK 320193, LEI HWUPKR0MPOU8FGXBT394) | LOW / 10 | REVIEW | TPRM-GOV-01 | 52 | all sources ok incl. GDELT (100 media items retrieved, 30 relevant; 2 subject, 20 victim, 8 mentioned) |
| The Boeing Company | BOEING CO | MEDIUM / 33 | REVIEW | TPRM-RSK-02, TPRM-PEP-01 | 49 | PEP potential match: director "Joyce David Leon" ~ Rep. David Joyce — a name collision the reviewer must discount; GDELT 429 |
| Sberbank | ПАО Сбербанк России (GLEIF, RU) | HIGH / 75 | ESCALATE | TPRM-SAN-01, TPRM-JUR-02, TPRM-RSK-01 | 9 | OFAC, EU and UK potential matches; GDELT 429 |
| Siemens AG | Siemens Aktiengesellschaft (GLEIF) | LOW / 0 | REVIEW | TPRM-GOV-01 | 26 | no SEC data (not an SEC registrant); no DOJ hits for the query; GDELT 429 |
| Microsoft Corp | MICROSOFT CORP | LOW / 22 | REVIEW | TPRM-GOV-01 | 39 | GDELT responded, no relevant GDELT items |

5. Review: reviewer approved the four REVIEW-tier cases; the ESCALATE case was not decidable by the reviewer and was
   rejected by the senior reviewer; the creator's own decision attempt → 403; a second decision → 409.
6. `GET /api/v1/audit/verify` → intact (40 records, including the six `source.refresh_succeeded` events). Direct `DELETE` on `audit_events` in PostgreSQL → rejected by the trigger ("audit_events is append-only").
7. Dashboard on the live stack: overview, case, financials, media and sources pages rendered with no browser console
   errors (screenshots `docs/images/live-*.png`).

An earlier run of the same script exposed a race when two background workers stored the same SEC file
simultaneously (unique-constraint violation, case degraded to "unresolved"). It was fixed (`SourceFetcher` reuses the
concurrent row) and covered by a unit test before the run above. Earlier runs also showed GDELT returning `{}` for
exact legal-name phrases over 3-month windows; the connector now queries the distinctive short name over a
configurable window (default one week), and the final run retrieved GDELT articles for Apple.

## 6. Static analysis and dependency audit

| Check | Result (2026-10-05) |
|---|---|
| `ruff check` / `ruff format --check` (src, tests, scripts) | clean |
| `python -m mypy src scripts` (104 files, pydantic plugin, project environment) + `gen_config_docs.py --check` | no issues; configuration reference in sync |
| `bandit -r src scripts -ll` (≈10.7 k LOC) | 0 medium/high findings |
| `pip-audit` on a clean virtualenv with `sentinel-dd[postgres]` (113 packages) | no known vulnerabilities (`docs/evidence/pip_audit.json`) |
| `npm audit --omit=dev` (dashboard) | 0 vulnerabilities |
| `tsc` + `vite build` | clean |

Trivy and gitleaks run in CI; they were not run in the release environment.
