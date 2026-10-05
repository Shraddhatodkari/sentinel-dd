# ADR-0007: Versioned scorecard instead of a supervised risk classifier

**Status:** Accepted

## Context
v1 trained a gradient-boosted risk classifier on synthetic labels. No public labelled dataset of third-party-risk
outcomes exists; a classifier trained on invented labels would present arbitrary output as a probability.

## Decision
Risk is computed by a deterministic, capped, additive scorecard over persisted signals (sanctions, PEP,
jurisdiction, adverse media as subject, financial stress, transparency, peer anomaly). Each factor records value,
rule, points, cap and evidence ids; the version is stored with each assessment. ML is used where real unlabelled
data supports it: an IsolationForest peer model on SEC XBRL frames, contributing at most 4 points.

## Consequences
+ Fully explainable and reproducible; reviewers see exactly why a score is what it is.
- Weights are expert-set; they must be calibrated against an organisation's historical decisions.
