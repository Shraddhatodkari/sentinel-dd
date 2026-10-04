"""Financial indicators from SEC XBRL company facts (reported values only — no estimation or imputation)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class FinancialIndicators:
    period_end: str | None = None
    revenue: float | None = None
    net_income: float | None = None
    total_assets: float | None = None
    total_liabilities: float | None = None
    equity: float | None = None
    current_ratio: float | None = None
    liabilities_to_assets: float | None = None
    net_margin: float | None = None
    return_on_assets: float | None = None
    revenue_growth: float | None = None
    flags: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _latest(facts: dict[str, Any], key: str) -> float | None:
    vals = facts.get(key) or []
    return float(vals[0]["value"]) if vals else None


def _prior(facts: dict[str, Any], key: str) -> float | None:
    vals = facts.get(key) or []
    return float(vals[1]["value"]) if len(vals) > 1 else None


def _ratio(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return round(a / b, 4)


def compute_indicators(facts: dict[str, Any]) -> FinancialIndicators:
    ind = FinancialIndicators()
    ind.period_end = (facts.get("total_assets") or facts.get("revenue") or [{}])[0].get("period_end")
    ind.revenue, ind.net_income = _latest(facts, "revenue"), _latest(facts, "net_income")
    ind.total_assets, ind.total_liabilities = _latest(facts, "total_assets"), _latest(facts, "total_liabilities")
    ind.equity = _latest(facts, "equity")
    if ind.total_liabilities is None and ind.total_assets is not None and ind.equity is not None:
        ind.total_liabilities = ind.total_assets - ind.equity  # accounting identity (A = L + E), flagged
        ind.flags.append("liabilities_derived_from_assets_minus_equity")
    ind.current_ratio = _ratio(_latest(facts, "current_assets"), _latest(facts, "current_liabilities"))
    ind.liabilities_to_assets = _ratio(ind.total_liabilities, ind.total_assets)
    ind.net_margin = _ratio(ind.net_income, ind.revenue)
    ind.return_on_assets = _ratio(ind.net_income, ind.total_assets)
    prev_rev = _prior(facts, "revenue")
    if ind.revenue is not None and prev_rev:
        ind.revenue_growth = round(ind.revenue / prev_rev - 1, 4)
    for k in ("revenue", "net_income", "total_assets", "equity"):
        if getattr(ind, k) is None:
            ind.missing.append(k)
    return ind


def financial_stress_points(ind: FinancialIndicators) -> list[tuple[str, float, str]]:
    """(rule_id, points, explanation) — each rule is a documented, reproducible threshold."""
    out: list[tuple[str, float, str]] = []
    if ind.equity is not None and ind.equity < 0:
        out.append(("FIN-NEG-EQUITY", 6, "reported stockholders' equity is negative"))
    if ind.net_income is not None and ind.net_income < 0:
        out.append(("FIN-NET-LOSS", 4, "reported net loss in latest fiscal year"))
    if ind.liabilities_to_assets is not None and ind.liabilities_to_assets > 0.9:
        out.append(("FIN-HIGH-LEVERAGE", 3, f"liabilities/assets = {ind.liabilities_to_assets:.2f} > 0.90"))
    if ind.current_ratio is not None and ind.current_ratio < 1.0:
        out.append(("FIN-LIQUIDITY", 2, f"current ratio = {ind.current_ratio:.2f} < 1.00"))
    if ind.revenue_growth is not None and ind.revenue_growth < -0.2:
        out.append(("FIN-REVENUE-DECLINE", 3, f"revenue change = {ind.revenue_growth:.1%} (< -20%)"))
    return out
