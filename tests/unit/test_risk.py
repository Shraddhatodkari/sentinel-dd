import json

import pytest

from sentinel.eval.harness import EVAL_DATA
from sentinel.governance.policy import Outcome, PolicyEngine, PolicyInput
from sentinel.risk import scorecard
from sentinel.risk.financial import FinancialIndicators, compute_indicators, financial_stress_points
from sentinel.risk.jurisdictions import country_code, tier_for
from sentinel.risk.media import classify, distinctive_tokens, subject_role


def test_media_taxonomy_and_relevance():
    c = classify(
        "Boeing Charged with 737 Max Fraud Conspiracy and Agrees to Pay over $2.5 Billion", ["The Boeing Company"]
    )
    assert c.relevant and {"financial_crime", "enforcement_action"} <= set(c.categories) and c.severity == "high"
    assert not classify("Appleton Man Sentenced", ["Apple Inc."]).relevant  # word-boundary relevance
    assert classify("Apple opens store", ["Apple Inc."]).categories == []
    assert distinctive_tokens("The Boeing Company") == ["boeing"]


def test_subject_role_on_labelled_real_titles():
    items = json.loads((EVAL_DATA / "media_roles.json").read_text())["items"]
    acc = sum(subject_role(i["title"], [i["entity"]]) == i["role"] for i in items) / len(items)
    assert acc >= 0.9
    assert subject_role("Justice Department Sues Apple", ["Apple Inc."]) == "subject"
    assert subject_role("Scheme to Defraud Apple Inc.", ["Apple Inc."]) == "victim"


def test_financial_indicators_and_stress():
    facts = {
        "revenue": [{"value": 80, "period_end": "2025-12-31"}, {"value": 120, "period_end": "2024-12-31"}],
        "net_income": [{"value": -5}],
        "total_assets": [{"value": 100}],
        "equity": [{"value": -2}],
        "current_assets": [{"value": 10}],
        "current_liabilities": [{"value": 20}],
    }
    ind = compute_indicators(facts)
    assert ind.total_liabilities == 102 and "liabilities_derived_from_assets_minus_equity" in ind.flags
    assert ind.current_ratio == 0.5 and ind.revenue_growth == pytest.approx(-0.3333, abs=1e-3)
    rules = {r for r, _, _ in financial_stress_points(ind)}
    assert rules == {"FIN-NEG-EQUITY", "FIN-NET-LOSS", "FIN-HIGH-LEVERAGE", "FIN-LIQUIDITY", "FIN-REVENUE-DECLINE"}
    assert compute_indicators({}).missing == ["revenue", "net_income", "total_assets", "equity"]
    assert financial_stress_points(FinancialIndicators()) == []


def test_jurisdiction_tiers():
    assert tier_for("KP") == ("prohibited", 40) and tier_for("RU")[0] == "high" and tier_for("US") == ("standard", 0)
    assert country_code("US-CA") == "US" and country_code("Russia") == "RU" and country_code(None) is None


def _sig(t, v, sev="high", **d):
    return {
        "signal_type": t,
        "value": v,
        "severity": sev,
        "confidence": 0.9,
        "evidence_ids": ["e1"],
        "detail": d,
        "label": t,
    }


FULL = {k: "ok" for k in scorecard.COVERAGE_WEIGHTS}
RES = {"name": "X", "confidence": 1.0, "lei": "L", "country": "US"}


def test_scorecard_bands_factors_and_forced_high():
    clean = scorecard.assess([], FULL, RES, {})
    assert clean.band == "LOW" and clean.score == 0 and clean.confidence == 1.0
    sanc = scorecard.assess(
        [_sig("sanctions_potential_match", 1.0, "critical", role="subject", source="ofac_sdn")], FULL, RES, {}
    )
    assert sanc.band == "HIGH" and sanc.score == 60 and "SANCTIONS-SUBJECT-FORCES-HIGH" in sanc.overrides
    weak = scorecard.assess(
        [_sig("sanctions_potential_match", 0.9, "critical", role="subject", source="x")], FULL, RES, {}
    )
    assert weak.band == "HIGH" and weak.score == 45  # still forced HIGH (potential match must be adjudicated)
    media = [_sig("adverse_media", 3, "high", source="doj_press")] * 4 + [
        _sig("adverse_media_unclear_role", 1, "low")
    ] * 10
    m = scorecard.assess(
        [*media, _sig("pep_potential_match", 0.9, "medium")],
        FULL,
        {**RES, "country": "RU"},
        {"lei_status": {"registration": "LAPSED", "entity": "INACTIVE"}},
    )
    f = {x.id: x for x in m.factors}
    assert f["ADVERSE_MEDIA"].points == 20 and f["PEP_EXPOSURE"].points == 10 and f["JURISDICTION"].points == 15
    assert f["TRANSPARENCY"].points == 10 and m.band == "HIGH" and f["ADVERSE_MEDIA"].evidence_ids == ["e1"]
    unresolved = scorecard.assess([], {"screening": "ok"}, {"name": None, "confidence": 0}, {})
    assert unresolved.confidence == 0 and {x.id: x for x in unresolved.factors}["TRANSPARENCY"].points == 10
    peer = scorecard.assess(
        [], FULL, RES, {}, {"available": True, "anomaly": True, "score": 0.1, "explanations": ["x"]}
    )
    assert {x.id: x for x in peer.factors}["PEER_ANOMALY"].points == 4
    nonsec = scorecard.assess([], {**FULL, "financials": "not_applicable"}, RES, {})
    assert nonsec.confidence == 0.95


def _pin(**kw):
    base = dict(
        band="LOW",
        score=5,
        confidence=1.0,
        sanctions_subject=[],
        sanctions_related=[],
        pep_matches=[],
        jurisdiction_tier="standard",
        resolution_confidence=1.0,
        ambiguous=False,
        resolved=True,
        coverage={"screening": "ok"},
        stale_sources=[],
        report_grounding=1.0,
        guardrail_violations=[],
    )
    return PolicyInput(**(base | kw))


def test_policy_rules_and_routing():
    strict = PolicyEngine("v", auto_clear_enabled=False)
    r = strict.evaluate(_pin())
    assert (
        r.outcome == Outcome.REVIEW
        and [x.rule_id for x in r.rules] == ["TPRM-GOV-01"]
        and r.required_role == "reviewer"
    )
    assert PolicyEngine("v", auto_clear_enabled=True).evaluate(_pin()).outcome == Outcome.AUTO_CLEAR
    esc = strict.evaluate(_pin(sanctions_subject=[{"evidence_ids": ["e"]}], band="HIGH"))
    assert esc.outcome == Outcome.ESCALATE and esc.required_role == "senior_reviewer"
    assert {x.rule_id for x in esc.rules} == {"TPRM-SAN-01", "TPRM-RSK-01"} and esc.rules[0].evidence_ids == ["e"]
    cases = {
        "TPRM-SAN-02": _pin(sanctions_related=[{"evidence_ids": []}]),
        "TPRM-JUR-01": _pin(jurisdiction_tier="prohibited"),
        "TPRM-JUR-02": _pin(jurisdiction_tier="high"),
        "TPRM-RSK-02": _pin(band="MEDIUM"),
        "TPRM-PEP-01": _pin(pep_matches=[{"evidence_ids": []}]),
        "TPRM-DAT-01": _pin(resolved=False),
        "TPRM-DAT-02": _pin(ambiguous=True),
        "TPRM-DAT-03": _pin(stale_sources=["ofac_sdn"]),
        "TPRM-DAT-04": _pin(coverage={"screening": "ok", "financials": "unavailable"}),
        "TPRM-DAT-05": _pin(confidence=0.2),
        "TPRM-AI-01": _pin(report_grounding=0.1),
        "TPRM-AI-02": _pin(guardrail_violations=["prohibited:overclaiming"]),
    }
    for rule, pin in cases.items():
        assert rule in [x.rule_id for x in strict.evaluate(pin).rules], rule
    d = esc.to_dict()
    assert d["outcome"] == "ESCALATE" and d["rules"][0]["outcome"] == "ESCALATE"
