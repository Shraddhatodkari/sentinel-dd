# Model card

Sentinel's assessment combines four analytical components. None of them is a regulatory determination.

## 1. Risk scorecard (`scorecard-2026.10`)

**Type:** deterministic, additive, capped. **Owner:** Compliance (configuration) / Engineering (code).

`score = min(100, Σ_f min(points_f, cap_f))`; bands **LOW < 25 ≤ MEDIUM < 50 ≤ HIGH**. A sanctions potential match
on the subject forces HIGH.

| Factor | Cap | Input (signals) | Points |
|---|---|---|---|
| `SANCTIONS_SUBJECT` | 60 | sanctions potential matches on the subject | 60 if best match score ≥ 0.95, else 45 if any match; any match also forces band HIGH |
| `SANCTIONS_RELATED` | 25 | matches on parents / subsidiaries / insiders | 25 × best match score |
| `PEP_EXPOSURE` | 15 | PEP potential matches | 10 for the first, +2 per additional |
| `JURISDICTION` | 40 | resolved country / jurisdiction | jurisdiction-policy tier points (default: prohibited 40, high 15, elevated 6) |
| `ADVERSE_MEDIA` | 20 | adverse-media signals with the entity as **subject** | DOJ high 6 / medium 3; news high 3 / medium 1.5; other 1; plus 0.5 per unclear-role item (≤ 3). Victim / mentioned items score 0 |
| `FINANCIAL_STRESS` | 15 | XBRL indicator rules (liquidity, leverage, losses, revenue decline) | sum of rule points |
| `TRANSPARENCY` | 10 | resolution + GLEIF status | unresolved 10; ambiguous +5; no LEI +2; LEI lapsed/retired/annulled +4; entity inactive +6 |
| `PEER_ANOMALY` | 4 | peer model | 4 if outlier |

Every factor stores: observed value, rules that fired, points, cap, evidence ids and `data_available`.
**Confidence** (0–1) = coverage score × entity-resolution confidence. Coverage weights: registry 0.30, screening
0.35, media 0.15, financials 0.10, filings 0.05, insiders 0.05; each counts 1.0 if `ok`, 0.6 if `partial`, 0 if
`unavailable`, and 1.0 if `not_applicable` (0.5 for financials, which remain a gap). It measures how complete the
evidence is — it is *not* a probability of wrongdoing.

**Calibration status:** weights and caps are expert-set defaults, not fitted to labelled outcomes (no public labelled
TPRM outcome dataset exists). The routing tests check that three reference entities land in the expected
band/outcome; this is a sanity check, not a validation of predictive accuracy. Calibrate against your own
historical decisions before production.

## 2. Peer anomaly model (`peer_anomaly`)

| | |
|---|---|
| Algorithm | scikit-learn `StandardScaler` + `IsolationForest` (300 trees, contamination 0.05, seed 7) |
| Training data | SEC XBRL *frames* API for one fiscal year (Assets, Liabilities, NetIncomeLoss, Revenues): every US filer reporting all inputs. Live run 2026-10-04, year 2024: **3 722 filers** |
| Features | liabilities/assets, return on assets, net margin, log10 assets (clipped) |
| Output | anomaly flag, anomaly score, per-feature peer percentiles, extreme features (≤ 2nd / ≥ 98th percentile) |
| Gates | n ≥ 1 000; anomaly rate within [0.02, 0.10]; agreement with a differently-seeded model ≥ 0.80. Live run: anomaly rate 0.050, seed agreement 0.990 |
| Use | `PEER_ANOMALY` factor (≤ 4 points) and an explanatory panel in the dashboard |
| Not for | detecting fraud or distress; ranking companies; any entity without SEC XBRL data (returns `available: false`, no imputation) |
| Known limits | cross-industry peers (banks and insurers are structurally "unusual"); one fiscal year; reporting-concept differences between filers |
| Monitoring | PSI per feature vs. reference sample (`sentinel ml drift`) |

## 3. Watch-list screening (`screening:rapidfuzz-v1`)

Normalisation (Unicode folding, punctuation, legal-form removal) → token blocking → RapidFuzz `token_sort_ratio` →
organisation subset matching weighted by token IDF across all list names → discount when the most distinctive
tokens differ → alias-strength (weak AKA ×0.92) and entity-type (×0.85) penalties → identifier matches (LEI, SWIFT,
registration numbers) score 1.0. Review threshold 0.88 (configurable).

Measured performance: see [evaluation.md](evaluation.md) — on the full official lists (33 649 records, 2026-10-04):
recall 1.0 on 224 perturbed real list names, 0.9 % of 1 000 real SEC registrant names produced a potential match.
Limitations: transliteration variants beyond Unicode folding (e.g. Cyrillic → Latin spellings that differ) are not
generated; date-of-birth / nationality are not used to discount person matches.

## 4. Adverse-media classifier (`media-taxonomy-2026.10`)

Keyword taxonomy (financial crime, sanctions/export controls, enforcement action, litigation, …) over titles and
abstracts, plus a rule-based **subject-role** classifier (subject / victim / mentioned) using the entity's names and
patterns such as "X charged", "X agrees to pay", "fraud against X", "former X employee". Accuracy on 24
hand-labelled real DOJ titles: **0.958**. Small labelled set; English only; titles about similarly named entities
can be mis-assigned.

## Versioning

Every assessment stores `model_versions = {scorecard, peer_model, media_taxonomy, screening}` and the feature vector.
Policy decisions store the policy version. Changing any of them requires a version bump and a new evaluation run.
