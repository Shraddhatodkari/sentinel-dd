# Model Card: Counter-party Risk Bundle (`risk_bundle`)

| Field | Value |
|---|---|
| Components | Calibrated GradientBoosting classifier (sigmoid), IsolationForest, K-Means (k=4), TF-IDF + LogReg media classifier |
| Intended use | Prioritise and explain due-diligence risk to **assist** compliance analysts |
| Out of scope | Fully automated rejection or onboarding without human oversight; credit decisions about individuals |
| Training data | Synthetic, domain-informed generator (`ml/features.py`). Replace with labelled case outcomes |
| Features | country risk, tenure, revenue (log), growth, leverage, late filings, cyber incidents, certifications, adverse-media count, sanctions proximity, PEP exposure, offshore layers |
| Metrics (holdout) | ROC-AUC ≈ 0.85, Brier ≈ 0.08–0.09, media macro-F1 ≈ 1.0 on templated data (optimistic) |
| Quality gates | AUC ≥ 0.78, Brier ≤ 0.15, media F1 ≥ 0.85; champion/challenger promotion |
| Explainability | Baseline-substitution local attributions (top-5 drivers) shown in every memo |
| Monitoring | PSI per feature; ≥ 2 features with PSI ≥ 0.25 → retrain recommended |
| Fairness | No protected attributes about natural persons are used. Country risk is a regulatory (FATF-style) input and is reviewed by the model-risk committee |
| Limitations | Synthetic training data; media classifier is lexical and English-only; negation handling is rule-based |
