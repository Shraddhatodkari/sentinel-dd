"""Versioned, explainable risk scorecard.

score = Σ factor points (each factor capped), clipped to [0, 100]. Every factor records its input value, the
rule that fired, the points awarded, the cap, and the evidence ids that support it — so the calculation is
fully reproducible from persisted data. Bands: LOW < 25 ≤ MEDIUM < 50 ≤ HIGH. A sanctions potential match on the
subject forces HIGH regardless of the arithmetic.

This is Sentinel's *analytical assessment*. It is not a regulatory determination.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from sentinel.risk.jurisdictions import load_policy, tier_for

SCORECARD_VERSION = "scorecard-2026.10"
BANDS = ((50.0, "HIGH"), (25.0, "MEDIUM"), (0.0, "LOW"))
CAPS = {
    "SANCTIONS_SUBJECT": 60,
    "SANCTIONS_RELATED": 25,
    "PEP_EXPOSURE": 15,
    "JURISDICTION": 40,
    "ADVERSE_MEDIA": 20,
    "FINANCIAL_STRESS": 15,
    "TRANSPARENCY": 10,
    "PEER_ANOMALY": 4,
}
# minimum data required for a fully-confident assessment and its weight in the confidence calculation
COVERAGE_WEIGHTS = {
    "corporate_registry": 0.3,
    "screening": 0.35,
    "adverse_media": 0.15,
    "financials": 0.1,
    "filings_text": 0.05,
    "insiders": 0.05,
}


@dataclass
class Factor:
    id: str
    name: str
    value: Any
    points: float
    cap: float
    rules: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    data_available: bool = True


@dataclass
class Assessment:
    version: str
    score: float
    band: str
    confidence: float
    factors: list[Factor]
    features: dict[str, Any]
    overrides: list[str] = field(default_factory=list)
    coverage: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _band(score: float) -> str:
    return next(b for threshold, b in BANDS if score >= threshold)


def assess(
    signals: list[dict[str, Any]],
    coverage: dict[str, str],
    resolution: dict[str, Any],
    registry_detail: dict[str, Any],
    peer: dict[str, Any] | None = None,
) -> Assessment:
    """``signals``: persisted Signal rows as dicts (signal_type, value, severity, confidence, evidence_ids, detail)."""
    by_type: dict[str, list[dict[str, Any]]] = {}
    for s in signals:
        by_type.setdefault(s["signal_type"], []).append(s)
    factors: list[Factor] = []
    overrides: list[str] = []

    # F1 / F2 sanctions
    sanc = by_type.get("sanctions_potential_match", [])
    subj = [s for s in sanc if s["detail"].get("role") == "subject"]
    rel = [s for s in sanc if s["detail"].get("role") != "subject"]
    best_subj = max((s["value"] for s in subj), default=0.0)
    pts = 60.0 if best_subj >= 0.95 else (45.0 if best_subj > 0 else 0.0)
    factors.append(
        Factor(
            "SANCTIONS_SUBJECT",
            "Sanctions potential match — subject",
            round(best_subj, 3),
            pts,
            60,
            [f"{len(subj)} potential match(es) on {len({s['detail'].get('source') for s in subj})} list(s)"]
            if subj
            else [],
            sorted({e for s in subj for e in s["evidence_ids"]}),
            coverage.get("screening") in ("ok", "partial"),
        )
    )
    if subj:
        overrides.append("SANCTIONS-SUBJECT-FORCES-HIGH")
    best_rel = max((s["value"] for s in rel), default=0.0)
    factors.append(
        Factor(
            "SANCTIONS_RELATED",
            "Sanctions potential match — related party",
            round(best_rel, 3),
            round(min(25.0, 25.0 * best_rel), 2),
            25,
            [f"{s['detail'].get('query')} ({s['detail'].get('role')}) on {s['detail'].get('source')}" for s in rel][:6],
            sorted({e for s in rel for e in s["evidence_ids"]}),
            coverage.get("screening") in ("ok", "partial"),
        )
    )

    # F3 PEP
    peps = by_type.get("pep_potential_match", [])
    factors.append(
        Factor(
            "PEP_EXPOSURE",
            "Politically exposed persons among insiders/related parties",
            len(peps),
            min(15.0, 10.0 + 2.0 * (len(peps) - 1)) if peps else 0.0,
            15,
            [s["label"] for s in peps][:6],
            sorted({e for s in peps for e in s["evidence_ids"]}),
            coverage.get("screening") in ("ok", "partial"),
        )
    )

    # F4 jurisdiction
    country = resolution.get("country") or resolution.get("jurisdiction")
    tier, jpts = tier_for(country)
    factors.append(
        Factor(
            "JURISDICTION",
            "Jurisdiction risk tier",
            {"country": country, "tier": tier},
            float(jpts),
            40,
            [f"{country or 'unknown'} → {tier} ({load_policy()['version']})"],
            [],
            country is not None,
        )
    )

    # F5 adverse media (subject role only drives material points)
    adv = by_type.get("adverse_media", [])
    unclear = by_type.get("adverse_media_unclear_role", [])
    media_pts = 0.0
    for s in adv:
        official = s["detail"].get("source") == "doj_press"
        media_pts += {("high", True): 6, ("high", False): 3, ("medium", True): 3, ("medium", False): 1.5}.get(
            (s["severity"], official), 1
        )
    media_pts += min(3.0, 0.5 * len(unclear))
    factors.append(
        Factor(
            "ADVERSE_MEDIA",
            "Adverse media / enforcement (entity as subject)",
            {"subject_items": len(adv), "unclear_role_items": len(unclear)},
            round(min(20.0, media_pts), 2),
            20,
            [s["label"] for s in adv][:8],
            sorted({e for s in adv + unclear for e in s["evidence_ids"]}),
            coverage.get("adverse_media") in ("ok", "partial"),
        )
    )

    # F6 financial stress
    fin = by_type.get("financial_stress", [])
    factors.append(
        Factor(
            "FINANCIAL_STRESS",
            "Financial stress indicators (SEC XBRL)",
            len(fin),
            min(15.0, sum(s["value"] for s in fin)),
            15,
            [f"{s['detail'].get('rule')}: {s['label']}" for s in fin],
            sorted({e for s in fin for e in s["evidence_ids"]}),
            coverage.get("financials") == "ok",
        )
    )

    # F7 transparency
    t_pts, t_rules = 0.0, []
    if not resolution.get("name"):
        t_pts, t_rules = 10.0, ["entity could not be resolved in SEC EDGAR or GLEIF"]
    else:
        if resolution.get("ambiguous"):
            t_pts += 5
            t_rules.append("ambiguous entity resolution")
        if not resolution.get("lei"):
            t_pts += 2
            t_rules.append("no Legal Entity Identifier found")
        status = registry_detail.get("lei_status") or {}
        if status.get("registration") in ("LAPSED", "RETIRED", "ANNULLED"):
            t_pts += 4
            t_rules.append(f"LEI registration status {status.get('registration')}")
        if status.get("entity") == "INACTIVE":
            t_pts += 6
            t_rules.append("GLEIF entity status INACTIVE")
    factors.append(
        Factor(
            "TRANSPARENCY",
            "Identity & registration transparency",
            t_rules or "clear",
            min(10.0, t_pts),
            10,
            t_rules,
            [],
        )
    )

    # F8 peer anomaly (ML, informational weight)
    if peer and peer.get("available"):
        factors.append(
            Factor(
                "PEER_ANOMALY",
                "Financial profile anomaly vs. SEC filer peers (IsolationForest)",
                {"anomaly": peer.get("anomaly"), "score": peer.get("score")},
                4.0 if peer.get("anomaly") else 0.0,
                4,
                peer.get("explanations", [])[:4],
                peer.get("evidence_ids", []),
            )
        )
    else:
        factors.append(
            Factor(
                "PEER_ANOMALY",
                "Financial profile anomaly vs. SEC filer peers (IsolationForest)",
                None,
                0.0,
                4,
                [
                    peer.get("reason", "model or financial data unavailable")
                    if peer
                    else "model or financial data unavailable"
                ],
                [],
                False,
            )
        )

    score = round(min(100.0, sum(f.points for f in factors)), 2)
    band = "HIGH" if "SANCTIONS-SUBJECT-FORCES-HIGH" in overrides else _band(score)

    def cov_weight(k: str) -> float:
        v = coverage.get(k)
        if v == "ok":
            return 1.0
        if v == "partial":
            return 0.6
        if v == "not_applicable":  # structurally absent (e.g. non-SEC entity): still a data gap for financials
            return 0.5 if k == "financials" else 1.0
        return 0.0

    cov_score = sum(w * cov_weight(k) for k, w in COVERAGE_WEIGHTS.items())
    confidence = round(max(0.0, min(1.0, cov_score * float(resolution.get("confidence") or 0.0))), 3)
    features = {f.id: f.value for f in factors}
    return Assessment(SCORECARD_VERSION, score, band, confidence, factors, features, overrides, coverage)
