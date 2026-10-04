"""Feature contract for the counter-party risk model + synthetic data generator.

The generator encodes domain knowledge (FATF jurisdiction risk, leverage, adverse media,
sanctions proximity, offshore layering) as a latent logit so models are trainable and
evaluable reproducibly in CI. In production, replace with labelled historical case outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FEATURES: list[str] = [
    "country_risk",
    "years_in_business",
    "log_revenue_musd",
    "revenue_growth",
    "debt_to_equity",
    "late_filings",
    "cyber_incidents",
    "certifications",
    "adverse_media_count",
    "sanctions_proximity",
    "pep_exposure",
    "offshore_layers",
]

COUNTRY_RISK: dict[str, float] = {
    "US": 0.10,
    "GB": 0.10,
    "DE": 0.08,
    "SG": 0.12,
    "IN": 0.30,
    "AE": 0.50,
    "CY": 0.55,
    "MU": 0.55,
    "PA": 0.75,
    "VG": 0.85,
    "KY": 0.70,
    "SC": 0.80,
    "IR": 1.0,
    "KP": 1.0,
    "MM": 0.95,
}

# true generative coefficients (used ONLY by the synthetic generator)
_TRUE_W = np.array([3.0, -0.06, -0.25, -2.4, 0.7, 0.55, 0.7, -0.5, 0.8, 4.5, 1.5, 1.0])
_TRUE_B = -2.6


@dataclass
class Dataset:
    X: np.ndarray
    y: np.ndarray
    features: list[str]


def generate_synthetic(n: int = 4000, seed: int = 7, shift: float = 0.0) -> Dataset:
    """``shift`` > 0 simulates population drift (riskier jurisdictions, more media)."""
    rng = np.random.default_rng(seed)
    country = np.clip(rng.beta(2, 5, n) + shift * 0.3, 0, 1)
    years = rng.gamma(3, 5, n).clip(0, 80)
    log_rev = rng.normal(3.5, 1.4, n).clip(-1, 9)
    growth = rng.normal(0.06, 0.18, n).clip(-0.9, 1.5)
    dte = rng.lognormal(-0.2 + shift * 0.3, 0.6, n).clip(0, 12)
    late = rng.poisson(0.4 + shift, n)
    cyber = rng.poisson(0.2, n)
    certs = rng.integers(0, 5, n)
    media = rng.poisson(0.5 + shift * 1.5, n)
    sanc = np.where(rng.random(n) < 0.06 + shift * 0.05, rng.choice([1.0, 0.5, 0.33], n), 0.0)
    pep = (rng.random(n) < 0.08).astype(float)
    offshore = rng.poisson(0.3 + shift * 0.5, n)
    X = np.column_stack([country, years, log_rev, growth, dte, late, cyber, certs, media, sanc, pep, offshore])
    logit = X @ _TRUE_W + _TRUE_B
    y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    return Dataset(X.astype(np.float64), y, list(FEATURES))


def vectorize(profile: dict[str, float]) -> np.ndarray:
    missing = [f for f in FEATURES if f not in profile]
    if missing:
        raise ValueError(f"missing features: {missing}")
    return np.array([[float(profile[f]) for f in FEATURES]])


def risk_band(p: float) -> str:
    if p >= 0.7:
        return "HIGH"
    if p >= 0.4:
        return "MEDIUM"
    return "LOW"
