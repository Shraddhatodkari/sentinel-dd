"""End-to-end training pipeline (MLOps): data -> train -> evaluate -> quality gate -> register ->
champion/challenger promotion. Runnable as ``sentinel-train`` or ``python -m sentinel.ml.train``."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any

import numpy as np

from sentinel.ml.features import FEATURES
from sentinel.ml.models import TrainedBundle, train_all
from sentinel.ml.registry import ModelRegistry

QUALITY_GATES = {"risk_roc_auc": 0.78, "media_macro_f1": 0.85}
MAX_BRIER = 0.15
PROMOTION_MARGIN = 0.0  # challenger must be >= champion AUC + margin


@dataclass
class TrainResult:
    version: int
    metrics: dict[str, float]
    passed_gates: bool
    promoted: bool
    reasons: list[str]


def check_gates(metrics: dict[str, float]) -> list[str]:
    failures = [f"{k}={metrics.get(k, 0):.3f} < {v}" for k, v in QUALITY_GATES.items() if metrics.get(k, 0) < v]
    if metrics.get("risk_brier", 1.0) > MAX_BRIER:
        failures.append(f"risk_brier={metrics['risk_brier']:.3f} > {MAX_BRIER}")
    return failures


def run_pipeline(registry: ModelRegistry, n: int = 4000, seed: int = 7) -> tuple[TrainResult, TrainedBundle]:
    bundle, Xref = train_all(n, seed)
    payload: dict[str, Any] = {"bundle": bundle, "reference_sample": Xref[:1000]}
    version = registry.register(
        "risk_bundle", payload, bundle.metrics, {"n": n, "seed": seed, "algo": "GBM+sigmoid-calibration"}, FEATURES
    )
    failures = check_gates(bundle.metrics)
    reasons = list(failures)
    promoted = False
    if not failures:
        champion = registry.latest("risk_bundle", "production")
        champ_auc = registry.meta("risk_bundle", champion)["metrics"]["risk_roc_auc"] if champion else -np.inf
        if bundle.metrics["risk_roc_auc"] >= champ_auc + PROMOTION_MARGIN:
            registry.set_stage("risk_bundle", version, "production")
            promoted = True
            reasons.append("promoted: challenger >= champion")
        else:
            registry.set_stage("risk_bundle", version, "staging")
            reasons.append(f"kept in staging: AUC {bundle.metrics['risk_roc_auc']:.3f} < champion {champ_auc:.3f}")
    return TrainResult(version, bundle.metrics, not failures, promoted, reasons), bundle


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Train & register Sentinel ML models")
    ap.add_argument("--model-dir", default="var/models")
    ap.add_argument("--n", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--mlflow-uri", default=None)
    args = ap.parse_args(argv)
    res, _ = run_pipeline(ModelRegistry(args.model_dir, args.mlflow_uri), args.n, args.seed)
    print(json.dumps(res.__dict__, indent=2, default=str))
    if not res.passed_gates:
        raise SystemExit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
