"""Online risk-scoring service: assembles features from structured, graph and text sources,
then runs the champion model bundle (classifier + anomaly + segmentation + media NLP)."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from sentinel.graphrag.graph import KnowledgeGraph
from sentinel.ml.features import COUNTRY_RISK, risk_band, vectorize
from sentinel.ml.models import TrainedBundle
from sentinel.observability.metrics import METRICS

_NEGATION = re.compile(r"^\s*(no|none|not|without)\b|\bno (adverse|litigation|enforcement)\b", re.I)
_SENT = re.compile(r"(?<=[.!?])\s+")


@dataclass
class RiskResult:
    entity: str
    probability: float
    band: str
    drivers: list[dict[str, Any]]
    anomaly: bool
    anomaly_score: float
    segment: str
    features: dict[str, float]
    media_hits: list[dict[str, Any]] = field(default_factory=list)
    model_version: str = "unversioned"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RiskEngine:
    def __init__(self, bundle: TrainedBundle, model_version: str = "unversioned", media_threshold: float = 0.5):
        self.bundle = bundle
        self.model_version = model_version
        self.media_threshold = media_threshold

    def classify_media(self, texts: list[tuple[str, str]]) -> list[dict[str, Any]]:
        """texts: [(citation, text)] -> adverse sentences with label/confidence."""
        sents = [(cid, s.strip()) for cid, t in texts for s in _SENT.split(t) if len(s.split()) >= 4]
        sents = [(cid, s) for cid, s in sents if not _NEGATION.search(s) and "[REMOVED" not in s]
        if not sents:
            return []
        probs = self.bundle.media.predict_proba([s for _, s in sents])
        hits = []
        for (cid, s), pr in zip(sents, probs, strict=True):
            label, conf = max(pr.items(), key=lambda kv: kv[1])
            if label != "neutral" and conf >= self.media_threshold:
                hits.append({"citation": cid, "sentence": s, "label": label, "confidence": round(conf, 3)})
        return hits

    @staticmethod
    def graph_features(graph: KnowledgeGraph, entity: str) -> dict[str, float]:
        sanc = graph.exposure(entity, "sanctioned", max_hops=3)
        pep = graph.exposure(entity, "pep", max_hops=1)
        return {
            "sanctions_proximity": round(1.0 / max(sanc[0].hops, 1), 3) if sanc else 0.0,
            "pep_exposure": 1.0 if pep else 0.0,
            "offshore_layers": float(graph.offshore_layers(entity)),
        }

    def build_features(
        self, profile: dict[str, Any] | None, graph_feats: dict[str, float], media_count: int
    ) -> dict[str, float]:
        p = profile or {}
        return {
            "country_risk": COUNTRY_RISK.get(str(p.get("country", "")).upper(), 0.5),  # unknown => cautious
            "years_in_business": float(p.get("years_in_business", 2)),
            "log_revenue_musd": math.log(max(float(p.get("revenue_musd", 1.0)), 0.1)),
            "revenue_growth": float(p.get("revenue_growth", 0.0)),
            "debt_to_equity": float(p.get("debt_to_equity", 1.5)),
            "late_filings": float(p.get("late_filings", 1)),
            "cyber_incidents": float(p.get("cyber_incidents", 0)),
            "certifications": float(p.get("certifications", 0)),
            "adverse_media_count": float(media_count),
            **graph_feats,
        }

    def score(
        self, entity: str, features: dict[str, float], media_hits: list[dict[str, Any]] | None = None
    ) -> RiskResult:
        x = vectorize(features)
        prob = float(self.bundle.risk.predict_proba(x)[0])
        METRICS.inc("risk_scored")
        return RiskResult(
            entity=entity,
            probability=round(prob, 4),
            band=risk_band(prob),
            drivers=self.bundle.risk.explain(x),
            anomaly=bool(self.bundle.anomaly.is_anomaly(x)[0]),
            anomaly_score=round(float(self.bundle.anomaly.score(x)[0]), 4),
            segment=self.bundle.segmenter.predict(x)[0],
            features=features,
            media_hits=media_hits or [],
            model_version=self.model_version,
        )
