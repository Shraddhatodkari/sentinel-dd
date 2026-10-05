# AI / LLM governance

## Principles

1. **LLMs do not create facts.** Facts come only from source documents. The default LLM backend (`extractive`)
   generates no text at all: it selects and cites sentences from retrieved evidence. A generative provider
   (Anthropic Claude, OpenAI, Bedrock, Vertex) can be enabled for planning and narrative, but its output must pass the
   same citation grounding check; ungrounded output lowers the report grounding score and triggers TPRM-AI-01.
2. **LLMs do not make decisions.** Risk scores come from a versioned scorecard; outcomes from a deterministic
   policy engine; final decisions from an authorised human. No agent or tool can record a review decision.
3. **Everything is versioned and reproducible.** Scorecard, jurisdiction policy, media taxonomy, screening method,
   peer model, prompt and LLM versions are stored with each assessment (`model_versions`); the policy version with each
   policy decision.
4. **Abstain rather than guess.** Research answers without sufficient evidence return `INSUFFICIENT_EVIDENCE`.

## Where AI/ML is used

| Component | Type | Decision role | Controls |
|---|---|---|---|
| Planner | LLM (optional) or default plan | chooses research questions | schema-validated output, deterministic fallback |
| Research Q&A | retrieval + extractive/LLM answer | evidence for analysts, part of the report | citations required, grounding score, injection-filtered context |
| Report writer | template + extractive/LLM narrative | report only | layer separation (FACT/SIGNAL/ASSESSMENT/POLICY), grounding, output guardrails, DLP |
| Critic | rule-based | triggers bounded re-research | max iterations |
| Screening | fuzzy matching (RapidFuzz) | produces potential matches | threshold in config, human adjudication mandatory |
| Media classifier | keyword taxonomy + subject-role rules | signals → scorecard factor | labelled evaluation set, versioned taxonomy |
| Peer model | IsolationForest | small scorecard factor (cap 4 of 100) | model card, quality gates, drift monitoring |
| Scorecard | deterministic arithmetic | analytical assessment | versioned, factor-level evidence |
| Policy | deterministic rules | outcome + required role | versioned, configuration-driven thresholds |

## Model lifecycle

* Training: `sentinel ml train --year Y` builds the dataset from SEC XBRL frames (real filer data only), trains,
  evaluates gates (minimum sample size, anomaly rate band, seed stability), registers with lineage (document ids of
  the frames) and promotes only if gates pass and it is not older than the current champion.
* The registry refuses to promote a model to production unless it is marked `provenance=real` with lineage, and verifies the artefact SHA-256 on every load.
* SFT export: `sentinel ml sft-export --as <auditor> --out var/sft` writes a DLP-masked chat dataset from
  human-approved cases only (research use; no fine-tuned model ships).
* Drift: `sentinel ml drift --year Y` computes PSI per feature against the model's reference sample; exit code 2
  recommends retraining.

## LLM operations

* Provider chain: primary + fallbacks (`SENTINEL_LLM_FALLBACKS`); the extractive backend is always appended last,
  so a provider outage degrades narrative quality instead of failing cases; failures are counted (`llm_errors`,
  `llm_fallbacks`).
* Usage: every call is metered (provider, model, purpose, case, tokens, latency, cost) into `llm_usage` and Prometheus;
  output tokens are capped per call (`SENTINEL_LLM_MAX_TOKENS`).
* Prompts live in `src/sentinel/prompts.py` with a `PROMPT_VERSION` (recorded per assessment); changes require
  re-running `sentinel eval`.
* Prompt injection: input guardrails on user text; retrieved chunks are scanned and excluded if flagged.

## Human oversight

* Auto-clear is disabled by default; every case requires a human decision.
* Sanctions potential matches always escalate to a senior reviewer.
* The reviewer sees the evidence, the factor breakdown, the rules that fired and the coverage gaps before deciding,
  and must write a rationale.

## Responsible-use statement

Sentinel assists due-diligence analysts. It does not determine whether an entity is sanctioned, whether a
relationship is lawful, or whether any regulation is satisfied. Outputs must be reviewed by qualified staff under the
organisation's own compliance policies.
