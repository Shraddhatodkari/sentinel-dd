"""MLOps pipeline for the peer-anomaly model: real SEC data → train → evaluate → gate → register → promote.

    sentinel ml train --year 2024

Quality gates (fail → model stays in staging, exit code 1):
  * ≥ 1,000 peer filers with complete inputs
  * training anomaly rate within [0.02, 0.10] (contamination sanity)
  * stability: ≥ 80% agreement of anomaly flags between two random seeds
Champion/challenger: a new version replaces production only if it trains on a period ≥ the champion's period.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from sentinel.ml.registry import ModelRegistry
from sentinel.risk.peer_model import FEATURES, MODEL_NAME, PeerAnomalyModel, PeerDataset, build_dataset
from sentinel.sources.fetcher import SourceFetcher

MIN_SAMPLES = 1000


@dataclass
class TrainResult:
    version: int
    metrics: dict[str, float]
    passed_gates: bool
    promoted: bool
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate(ds: PeerDataset, model: PeerAnomalyModel) -> dict[str, float]:
    alt = PeerAnomalyModel(random_state=11).fit(ds)
    a = model.pipe.predict(ds.X) == -1
    b = alt.pipe.predict(ds.X) == -1
    return {"n_samples": float(len(ds.X)), "anomaly_rate": float(np.mean(a)), "seed_agreement": float(np.mean(a == b))}


def gates(metrics: dict[str, float]) -> list[str]:
    fails = []
    if metrics["n_samples"] < MIN_SAMPLES:
        fails.append(f"n_samples {int(metrics['n_samples'])} < {MIN_SAMPLES}")
    if not 0.02 <= metrics["anomaly_rate"] <= 0.10:
        fails.append(f"anomaly_rate {metrics['anomaly_rate']:.3f} outside [0.02, 0.10]")
    if metrics["seed_agreement"] < 0.8:
        fails.append(f"seed_agreement {metrics['seed_agreement']:.3f} < 0.80")
    return fails


def train_and_register(registry: ModelRegistry, ds: PeerDataset) -> TrainResult:
    model = PeerAnomalyModel().fit(ds)
    metrics = evaluate(ds, model)
    version = registry.register(
        MODEL_NAME,
        model,
        metrics,
        {"algo": "IsolationForest", "contamination": 0.05, "period": ds.period},
        FEATURES,
        provenance="real",
        lineage={"source": "SEC XBRL frames", "document_ids": ds.document_ids, "period": ds.period},
    )
    fails = gates(metrics)
    if fails:
        registry.set_stage(MODEL_NAME, version, "staging")
        return TrainResult(version, metrics, False, False, fails)
    champ = registry.latest(MODEL_NAME, "production")
    if champ is not None and registry.meta(MODEL_NAME, champ)["params"].get("period", "") > ds.period:
        registry.set_stage(MODEL_NAME, version, "staging")
        return TrainResult(version, metrics, True, False, ["champion trained on a more recent period"])
    registry.set_stage(MODEL_NAME, version, "production")
    return TrainResult(version, metrics, True, True, ["promoted"])


def run(fetcher: SourceFetcher, registry: ModelRegistry, year: int) -> TrainResult:
    return train_and_register(registry, build_dataset(fetcher, year))
