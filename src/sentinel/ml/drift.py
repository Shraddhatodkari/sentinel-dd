"""Data-drift monitoring via Population Stability Index (PSI), per feature."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


def psi(expected: np.ndarray, actual: np.ndarray, bins: int = 10, eps: float = 1e-4) -> float:
    edges = np.unique(np.quantile(expected, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:  # near-constant feature: fall back to equal-width bins over the union
        lo, hi = min(expected.min(), actual.min()), max(expected.max(), actual.max())
        edges = np.linspace(lo, hi + eps, bins + 1)
    edges[0], edges[-1] = -np.inf, np.inf
    e = np.histogram(expected, edges)[0] / len(expected)
    a = np.histogram(actual, edges)[0] / len(actual)
    e, a = np.clip(e, eps, None), np.clip(a, eps, None)
    return float(np.sum((a - e) * np.log(a / e)))


def status(value: float) -> str:
    return "stable" if value < 0.1 else ("moderate" if value < 0.25 else "significant")


@dataclass
class DriftReport:
    per_feature: dict[str, float] = field(default_factory=dict)

    @property
    def drifted(self) -> list[str]:
        return [f for f, v in self.per_feature.items() if v >= 0.25]

    @property
    def overall(self) -> str:
        if self.drifted:
            return "significant"
        return "moderate" if any(v >= 0.1 for v in self.per_feature.values()) else "stable"

    @property
    def retrain_recommended(self) -> bool:
        return len(self.drifted) >= 2


def drift_report(reference: np.ndarray, current: np.ndarray, features: list[str]) -> DriftReport:
    return DriftReport({f: round(psi(reference[:, i], current[:, i]), 4) for i, f in enumerate(features)})
