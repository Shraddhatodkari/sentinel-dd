"""Peer-anomaly model trained on *real* SEC XBRL frames (all filers reporting a concept for a calendar period).

Features per filer (no imputation; filers missing any input are excluded):
  liabilities_to_assets, return_on_assets (clipped), net_margin (clipped), log10(total assets)

Model: StandardScaler + IsolationForest. Output for a subject: anomaly flag, anomaly score and per-feature
peer percentiles (the explanation shown to reviewers). This is an *informational* signal with a small,
capped scorecard weight — it describes how unusual a profile is, not whether an entity is risky.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from sentinel.risk.financial import FinancialIndicators
from sentinel.sources.fetcher import SourceFetcher

FEATURES = ["liabilities_to_assets", "return_on_assets", "net_margin", "log_assets"]
FRAME_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/USD/{period}.json"
MODEL_NAME = "peer_anomaly"


@dataclass
class PeerDataset:
    X: np.ndarray
    ciks: list[int]
    period: str
    document_ids: list[str]


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def feature_row(liab: float | None, assets: float | None, ni: float | None, rev: float | None) -> list[float] | None:
    if not assets or assets <= 0 or liab is None or ni is None or not rev or rev <= 0:
        return None
    return [_clip(liab / assets, 0, 5), _clip(ni / assets, -2, 2), _clip(ni / rev, -3, 1), math.log10(assets)]


def build_dataset(fetcher: SourceFetcher, year: int) -> PeerDataset:
    inst, dur = f"CY{year}Q4I", f"CY{year}"
    docs: list[str] = []

    def frame(concept: str, period: str) -> dict[int, float]:
        d = fetcher.get("sec_edgar", FRAME_URL.format(concept=concept, period=period))
        docs.append(d.document_id)
        return {int(r["cik"]): float(r["val"]) for r in d.json().get("data", [])}

    assets, liab = frame("Assets", inst), frame("Liabilities", inst)
    ni = frame("NetIncomeLoss", dur)
    rev = frame("Revenues", dur) | {
        k: v for k, v in frame("RevenueFromContractWithCustomerExcludingAssessedTax", dur).items()
    }
    rows, ciks = [], []
    for cik, a in assets.items():
        row = feature_row(liab.get(cik), a, ni.get(cik), rev.get(cik))
        if row is not None:
            rows.append(row)
            ciks.append(cik)
    return PeerDataset(np.asarray(rows, dtype=float), ciks, f"{dur}/{inst}", docs)


class PeerAnomalyModel:
    def __init__(self, contamination: float = 0.05, random_state: int = 7) -> None:
        self.pipe = Pipeline(
            [
                ("scale", StandardScaler()),
                ("iso", IsolationForest(n_estimators=300, contamination=contamination, random_state=random_state)),
            ]
        )
        self.reference: np.ndarray | None = None
        self.meta: dict[str, Any] = {}

    def fit(self, ds: PeerDataset) -> PeerAnomalyModel:
        if len(ds.X) < 200:
            raise ValueError(f"insufficient peer data ({len(ds.X)} filers) to fit a reliable model")
        self.pipe.fit(ds.X)
        self.reference = ds.X
        self.meta = {
            "period": ds.period,
            "n_samples": len(ds.X),
            "features": FEATURES,
            "provenance": "real",
            "source": "SEC XBRL frames API",
            "document_ids": ds.document_ids,
            "train_anomaly_rate": float(np.mean(self.pipe.predict(ds.X) == -1)),
        }
        return self

    def score(self, ind: FinancialIndicators) -> dict[str, Any]:
        row = feature_row(ind.total_liabilities, ind.total_assets, ind.net_income, ind.revenue)
        if row is None or self.reference is None:
            return {"available": False, "reason": "required financial inputs not reported (no imputation)"}
        x = np.asarray([row])
        anomalous = bool(self.pipe.predict(x)[0] == -1)
        score = float(-self.pipe.decision_function(x)[0])
        pct = {f: round(float(np.mean(self.reference[:, i] <= row[i])) * 100, 1) for i, f in enumerate(FEATURES)}
        expl = [f"{f} = {row[i]:.3f} (peer percentile {pct[f]})" for i, f in enumerate(FEATURES)]
        extreme = [f for f, p in pct.items() if p <= 2 or p >= 98]
        return {
            "available": True,
            "anomaly": anomalous,
            "score": round(score, 4),
            "percentiles": pct,
            "values": dict(zip(FEATURES, [round(v, 4) for v in row], strict=True)),
            "extreme_features": extreme,
            "explanations": expl,
            "model_period": self.meta.get("period"),
            "n_peers": self.meta.get("n_samples"),
        }
