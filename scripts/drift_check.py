"""Scheduled MLOps job: compare live feature distribution to the training reference (PSI).

Exit code 2 => retraining recommended (wire to Airflow / Vertex AI Pipelines / GitHub Actions cron).
"""

from __future__ import annotations

import argparse
import json
import sys

from sentinel.ml.drift import drift_report
from sentinel.ml.features import FEATURES, generate_synthetic
from sentinel.ml.registry import ModelRegistry


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default="var/models")
    ap.add_argument("--simulate-shift", type=float, default=0.0)
    args = ap.parse_args()
    payload, meta = ModelRegistry(args.model_dir).load("risk_bundle")
    current = generate_synthetic(2000, seed=99, shift=args.simulate_shift).X  # replace with last-30-days features
    rep = drift_report(payload["reference_sample"], current, FEATURES)
    print(json.dumps({"model": f"v{meta['version']}", "overall": rep.overall, "drifted": rep.drifted,
                      "psi": rep.per_feature}, indent=2))
    return 2 if rep.retrain_recommended else 0


if __name__ == "__main__":
    sys.exit(main())
