"""Model definitions: supervised risk classifier, unsupervised anomaly detector & segmentation,
and an NLP adverse-media classifier."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.cluster import KMeans
from sklearn.ensemble import GradientBoostingClassifier, IsolationForest
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    f1_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from sentinel.ml.features import FEATURES


# --------------------------------------------------------------------------- supervised
class RiskModel:
    """Calibrated gradient-boosting classifier with local explanations."""

    def __init__(self, random_state: int = 7) -> None:
        base = GradientBoostingClassifier(
            n_estimators=200, max_depth=3, learning_rate=0.05, subsample=0.9, random_state=random_state
        )
        self.model = CalibratedClassifierCV(base, method="sigmoid", cv=3)
        self.baseline: np.ndarray | None = None
        self.features = list(FEATURES)

    def fit(self, X: np.ndarray, y: np.ndarray) -> RiskModel:
        self.model.fit(X, y)
        self.baseline = np.median(X, axis=0)
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self.model.predict_proba(X)[:, 1]

    def explain(self, x: np.ndarray, top_k: int = 5) -> list[dict[str, Any]]:
        """Baseline-substitution attributions: Δp when a feature is reset to the population median."""
        assert self.baseline is not None, "model not fitted"
        p = float(self.predict_proba(x)[0])
        contribs: list[dict[str, Any]] = []
        for i, name in enumerate(self.features):
            x2 = x.copy()
            x2[0, i] = self.baseline[i]
            delta = p - float(self.predict_proba(x2)[0])
            contribs.append({"feature": name, "value": float(x[0, i]), "contribution": round(delta, 4)})
        return sorted(contribs, key=lambda c: -abs(c["contribution"]))[:top_k]

    @staticmethod
    def evaluate(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
        return {
            "roc_auc": float(roc_auc_score(y, p)),
            "pr_auc": float(average_precision_score(y, p)),
            "brier": float(brier_score_loss(y, p)),
            "positive_rate": float(np.mean(y)),
        }


# --------------------------------------------------------------------------- unsupervised
class AnomalyDetector:
    def __init__(self, contamination: float = 0.05, random_state: int = 7) -> None:
        self.pipe = Pipeline(
            [
                ("scale", StandardScaler()),
                ("iso", IsolationForest(n_estimators=200, contamination=contamination, random_state=random_state)),
            ]
        )

    def fit(self, X: np.ndarray) -> AnomalyDetector:
        self.pipe.fit(X)
        return self

    def score(self, X: np.ndarray) -> np.ndarray:
        """Higher = more anomalous."""
        return -self.pipe.decision_function(X)

    def is_anomaly(self, X: np.ndarray) -> np.ndarray:
        return self.pipe.predict(X) == -1


class Segmenter:
    """K-Means portfolio segmentation; segments are named by their mean observed risk."""

    def __init__(self, k: int = 4, random_state: int = 7) -> None:
        self.pipe = Pipeline(
            [("scale", StandardScaler()), ("km", KMeans(n_clusters=k, n_init=10, random_state=random_state))]
        )
        self.names: dict[int, str] = {}

    def fit(self, X: np.ndarray, y: np.ndarray | None = None) -> Segmenter:
        labels = self.pipe.fit_predict(X)
        k = self.pipe.named_steps["km"].n_clusters
        risk = [float(np.mean(y[labels == c])) if y is not None and np.any(labels == c) else 0.0 for c in range(k)]
        tiers = ["low-risk core", "watch", "elevated", "critical"]
        for rank, c in enumerate(np.argsort(risk)):
            self.names[int(c)] = tiers[min(rank, len(tiers) - 1)] if k <= 4 else f"segment-{rank}"
        return self

    def predict(self, X: np.ndarray) -> list[str]:
        return [self.names[int(c)] for c in self.pipe.predict(X)]


# --------------------------------------------------------------------------- NLP
MEDIA_LABELS = ["fraud", "sanctions", "cyber", "litigation", "neutral"]

_TEMPLATES: dict[str, list[str]] = {
    "fraud": [
        "{e} under investigation for customs fraud",
        "authorities probe {e} over invoice manipulation",
        "{e} executives charged with money laundering",
        "regulator alleges {e} falsified accounts",
        "{e} linked to trade-based money laundering scheme",
        "whistleblower accuses {e} of bribery",
        "{e} raided in tax evasion inquiry",
        "{e} accused of misappropriating client funds",
    ],
    "sanctions": [
        "{e} director designated under sanctions",
        "{e} shipments breached export controls",
        "{e} added to sanctions watch list",
        "{e} accused of evading trade embargo",
        "assets of {e} owner frozen by treasury",
        "{e} transacted with blocked persons",
    ],
    "cyber": [
        "{e} suffers data breach exposing customer records",
        "ransomware attack disrupts {e} operations",
        "{e} fined for leaking personal data",
        "hackers stole credentials from {e} systems",
        "{e} discloses unauthorized access to its cloud database",
    ],
    "litigation": [
        "{e} faces lawsuit from former partner",
        "court rules against {e} in contract dispute",
        "{e} settles class action over billing",
        "{e} sued for patent infringement",
        "arbitration claim filed against {e}",
    ],
    "neutral": [
        "{e} opens new warehouse in Pune",
        "{e} wins logistics excellence award",
        "{e} reports steady quarterly growth",
        "{e} appoints new chief operating officer",
        "{e} achieves ISO 27001 recertification",
        "{e} expands partnership with regional carriers",
        "{e} publishes annual sustainability report",
        "{e} launches new analytics platform",
    ],
}
_NAMES = [
    "Acme Corp",
    "Zenith Ltd",
    "Orion Traders",
    "Kestrel Inc",
    "Lotus Exports",
    "Vertex Logistics",
    "Nimbus Systems",
    "Granite Holdings",
    "Sable Shipping",
    "Aurora Foods",
]


def generate_media_corpus(seed: int = 7) -> tuple[list[str], list[str]]:
    rng = np.random.default_rng(seed)
    texts, labels = [], []
    prefixes = ["", "Report: ", "Breaking: ", "Sources say ", "Update - "]
    for label, tmpls in _TEMPLATES.items():
        for t in tmpls:
            for name in _NAMES:
                texts.append(str(rng.choice(prefixes)) + t.format(e=name))
                labels.append(label)
    return texts, labels


class AdverseMediaClassifier:
    def __init__(self) -> None:
        self.pipe = Pipeline(
            [
                ("tfidf", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=1)),
                ("clf", LogisticRegression(max_iter=2000, C=4.0, class_weight="balanced")),
            ]
        )

    def fit(self, texts: list[str], labels: list[str]) -> AdverseMediaClassifier:
        self.pipe.fit(texts, labels)
        return self

    def predict(self, texts: list[str]) -> list[str]:
        return list(self.pipe.predict(texts))

    def predict_proba(self, texts: list[str]) -> list[dict[str, float]]:
        classes = list(self.pipe.classes_)
        return [dict(zip(classes, map(float, row), strict=True)) for row in self.pipe.predict_proba(texts)]

    def evaluate(self, texts: list[str], labels: list[str]) -> dict[str, float]:
        return {"macro_f1": float(f1_score(labels, self.predict(texts), average="macro"))}


@dataclass
class TrainedBundle:
    risk: RiskModel
    anomaly: AnomalyDetector
    segmenter: Segmenter
    media: AdverseMediaClassifier
    metrics: dict[str, float]


def train_all(n: int = 4000, seed: int = 7) -> tuple[TrainedBundle, np.ndarray]:
    from sentinel.ml.features import generate_synthetic

    ds = generate_synthetic(n, seed)
    Xtr, Xte, ytr, yte = train_test_split(ds.X, ds.y, test_size=0.25, random_state=seed, stratify=ds.y)
    risk = RiskModel(seed).fit(Xtr, ytr)
    metrics = {f"risk_{k}": v for k, v in RiskModel.evaluate(yte, risk.predict_proba(Xte)).items()}
    anomaly = AnomalyDetector(random_state=seed).fit(Xtr)
    metrics["anomaly_rate_test"] = float(np.mean(anomaly.is_anomaly(Xte)))
    seg = Segmenter(random_state=seed).fit(Xtr, ytr)
    texts, labels = generate_media_corpus(seed)
    ttr, tte, ltr, lte = train_test_split(texts, labels, test_size=0.25, random_state=seed, stratify=labels)
    media = AdverseMediaClassifier().fit(ttr, ltr)
    metrics |= {f"media_{k}": v for k, v in media.evaluate(tte, lte).items()}
    return TrainedBundle(risk, anomaly, seg, media, metrics), Xtr
