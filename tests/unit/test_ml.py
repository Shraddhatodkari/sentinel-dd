import json

import numpy as np
import pytest

from sentinel.graphrag.graph import KnowledgeGraph
from sentinel.ml.drift import drift_report, psi, status
from sentinel.ml.features import FEATURES, generate_synthetic, risk_band, vectorize
from sentinel.ml.models import AdverseMediaClassifier, generate_media_corpus
from sentinel.ml.registry import ModelRegistry, RegistryError
from sentinel.ml.scoring import RiskEngine
from sentinel.ml.train import check_gates, main, run_pipeline

LOW = {
    "country_risk": 0.1,
    "years_in_business": 25,
    "log_revenue_musd": 5.5,
    "revenue_growth": 0.1,
    "debt_to_equity": 0.5,
    "late_filings": 0,
    "cyber_incidents": 0,
    "certifications": 4,
    "adverse_media_count": 0,
    "sanctions_proximity": 0,
    "pep_exposure": 0,
    "offshore_layers": 0,
}
HIGH = LOW | {
    "country_risk": 0.85,
    "years_in_business": 2,
    "revenue_growth": -0.3,
    "debt_to_equity": 4,
    "late_filings": 3,
    "certifications": 0,
    "adverse_media_count": 4,
    "sanctions_proximity": 1.0,
    "offshore_layers": 3,
}


def test_synthetic_data_is_reproducible_and_drifts():
    a, b = generate_synthetic(500, 1), generate_synthetic(500, 1)
    assert np.array_equal(a.X, b.X) and a.X.shape == (500, len(FEATURES))
    assert 0.03 < a.y.mean() < 0.4
    shifted = generate_synthetic(500, 2, shift=1.0)
    assert shifted.y.mean() > a.y.mean()


def test_bundle_quality(bundle):
    m = bundle.metrics
    assert m["risk_roc_auc"] > 0.75 and m["risk_brier"] < 0.15 and m["media_macro_f1"] > 0.85
    assert 0.0 < m["anomaly_rate_test"] < 0.15
    assert check_gates(m) == []
    assert check_gates({"risk_roc_auc": 0.5, "media_macro_f1": 0.9, "risk_brier": 0.3})


def test_risk_monotonic_and_explanations(bundle):
    p_low = bundle.risk.predict_proba(vectorize(LOW))[0]
    p_high = bundle.risk.predict_proba(vectorize(HIGH))[0]
    assert p_low < 0.2 < 0.7 < p_high
    drivers = bundle.risk.explain(vectorize(HIGH), top_k=3)
    assert len(drivers) == 3 and all(d["feature"] in FEATURES for d in drivers)
    assert abs(drivers[0]["contribution"]) >= abs(drivers[-1]["contribution"])
    with pytest.raises(ValueError):
        vectorize({"country_risk": 1})
    assert [risk_band(x) for x in (0.1, 0.5, 0.9)] == ["LOW", "MEDIUM", "HIGH"]


def test_anomaly_and_segments(bundle):
    weird = vectorize(HIGH | {"years_in_business": 80, "debt_to_equity": 12, "adverse_media_count": 15})
    assert bundle.anomaly.is_anomaly(weird)[0]
    assert bundle.anomaly.score(weird)[0] > bundle.anomaly.score(vectorize(LOW))[0]
    assert bundle.segmenter.predict(vectorize(LOW))[0] in {"low-risk core", "watch", "elevated", "critical"}


def test_media_classifier():
    t, lab = generate_media_corpus()
    clf = AdverseMediaClassifier().fit(t, lab)
    assert clf.predict(["Omega Ltd under investigation for customs fraud"]) == ["fraud"]
    assert clf.predict(["Omega Ltd suffers data breach exposing customer records"]) == ["cyber"]
    assert abs(sum(clf.predict_proba(["Omega opens new warehouse"])[0].values()) - 1) < 1e-6


def test_risk_engine_features_and_negation(bundle):
    eng = RiskEngine(bundle, "v-test")
    hits = eng.classify_media(
        [
            (
                "n#0",
                "No adverse media was identified for Omega. Omega Ltd under investigation "
                "for customs fraud and money laundering. Short one.",
            )
        ]
    )
    assert len(hits) == 1 and hits[0]["label"] == "fraud" and hits[0]["citation"] == "n#0"
    assert eng.classify_media([]) == []
    feats = eng.build_features(None, {"sanctions_proximity": 0, "pep_exposure": 0, "offshore_layers": 0}, 0)
    assert feats["country_risk"] == 0.5  # unknown -> cautious default
    r = eng.score("Omega", vectorize_dict(HIGH))
    assert r.band == "HIGH" and r.model_version == "v-test" and r.to_dict()["entity"] == "Omega"
    g = KnowledgeGraph()
    assert eng.graph_features(g, "Nobody") == {"sanctions_proximity": 0.0, "pep_exposure": 0.0, "offshore_layers": 0.0}


def vectorize_dict(d):
    return dict(d)


def test_drift_detection():
    ref = generate_synthetic(2000, 1).X
    same = generate_synthetic(2000, 2).X
    shifted = generate_synthetic(2000, 3, shift=1.5).X
    assert drift_report(ref, same, FEATURES).overall in ("stable", "moderate")
    rep = drift_report(ref, shifted, FEATURES)
    assert rep.overall == "significant" and rep.retrain_recommended and "adverse_media_count" in rep.drifted
    assert psi(np.zeros(100), np.zeros(100)) == pytest.approx(0, abs=1e-6)
    assert [status(x) for x in (0.05, 0.2, 0.5)] == ["stable", "moderate", "significant"]


def test_registry_versioning_stages_integrity(tmp_path):
    reg = ModelRegistry(tmp_path)
    assert reg.versions("m") == [] and reg.latest("m") is None
    v1 = reg.register("m", {"w": 1}, {"auc": 0.8})
    v2 = reg.register("m", {"w": 2}, {"auc": 0.9})
    assert (v1, v2) == (1, 2)
    reg.set_stage("m", v1, "production")
    reg.set_stage("m", v2, "production")
    assert reg.meta("m", v1)["stage"] == "archived" and reg.load("m")[0] == {"w": 2}
    with pytest.raises(RegistryError):
        reg.set_stage("m", v1, "bogus")
    (tmp_path / "m" / "v2" / "model.pkl").write_bytes(b"tampered")
    with pytest.raises(RegistryError, match="integrity"):
        reg.load("m")
    with pytest.raises(RegistryError):
        reg.load("other")


def test_training_pipeline_champion_challenger(tmp_path, capsys):
    reg = ModelRegistry(tmp_path)
    r1, _ = run_pipeline(reg, n=1500, seed=3)
    assert r1.passed_gates and r1.promoted and reg.latest("risk_bundle", "production") == 1
    r2, _ = run_pipeline(reg, n=600, seed=11)  # weaker challenger
    assert reg.latest("risk_bundle", "production") in (1, 2)
    assert r2.promoted or any("staging" in x for x in r2.reasons) or not r2.passed_gates
    main(["--model-dir", str(tmp_path / "cli"), "--n", "1500", "--seed", "3"])
    out = json.loads(capsys.readouterr().out)
    assert out["promoted"] is True
