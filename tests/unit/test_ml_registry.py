import numpy as np
import pytest

from sentinel.ml.drift import drift_report, psi, status
from sentinel.ml.registry import ModelRegistry, RegistryError
from sentinel.ml.train import gates, train_and_register
from sentinel.risk.financial import FinancialIndicators
from sentinel.risk.peer_model import FEATURES, PeerAnomalyModel, PeerDataset, build_dataset, feature_row


def test_peer_dataset_from_recorded_sec_frames(container):
    ds = build_dataset(container.fetcher, 2024)
    assert len(ds.X) > 3000 and ds.X.shape[1] == len(FEATURES) and len(ds.document_ids) == 5
    assert feature_row(None, 10, 1, 1) is None and feature_row(5, 10, 1, 2) == pytest.approx([0.5, 0.1, 0.5, 1.0])


def test_peer_model_scoring_and_explanations(container):
    m = container.peer_model
    assert m is not None and container.peer_model_version == "peer_anomaly:v1"
    typical = FinancialIndicators(revenue=1e9, net_income=5e7, total_assets=2e9, total_liabilities=1.2e9)
    # mega-cap with ~100% net margin and almost no liabilities: extreme on three features vs. real SEC peers
    weird = FinancialIndicators(revenue=5e11, net_income=4.9e11, total_assets=5e11, total_liabilities=1e6)
    t, w = m.score(typical), m.score(weird)
    assert t["available"] and not t["anomaly"] and w["anomaly"] and w["score"] > t["score"]
    assert w["extreme_features"] and len(w["explanations"]) == 4
    assert m.score(FinancialIndicators())["available"] is False
    with pytest.raises(ValueError, match="insufficient"):
        PeerAnomalyModel().fit(PeerDataset(np.zeros((10, 4)), [], "p", []))


def test_registry_provenance_gate_integrity_and_champion(tmp_path, container):
    reg = ModelRegistry(tmp_path)
    v = reg.register("m", {"w": 1}, {"auc": 0.8}, provenance="synthetic")
    with pytest.raises(RegistryError, match="provenance"):
        reg.set_stage("m", v, "production")
    with pytest.raises(RegistryError):
        reg.set_stage("m", v, "bogus")
    reg.set_stage("m", v, "staging")
    v2 = reg.register("m", {"w": 2}, {}, provenance="real", lineage={"doc": "x"})
    reg.set_stage("m", v2, "production")
    v3 = reg.register("m", {"w": 3}, {}, provenance="real", lineage={"doc": "y"})
    reg.set_stage("m", v3, "production")
    assert reg.meta("m", v2)["stage"] == "archived" and reg.load("m")[0] == {"w": 3}
    (tmp_path / "m" / f"v{v3}" / "model.pkl").write_bytes(b"tampered")
    with pytest.raises(RegistryError, match="integrity"):
        reg.load("m")
    with pytest.raises(RegistryError):
        reg.load("nothing")
    ds = build_dataset(container.fetcher, 2024)
    res = train_and_register(ModelRegistry(tmp_path / "r"), ds)
    assert res.promoted and res.passed_gates and 0.02 <= res.metrics["anomaly_rate"] <= 0.1
    small = PeerDataset(ds.X[:300], ds.ciks[:300], ds.period, ds.document_ids)
    res2 = train_and_register(ModelRegistry(tmp_path / "r"), small)
    assert not res2.promoted and any("n_samples" in r for r in res2.reasons)
    assert (
        gates({"n_samples": 5000, "anomaly_rate": 0.5, "seed_agreement": 0.1})
        and len(gates({"n_samples": 5000, "anomaly_rate": 0.05, "seed_agreement": 0.9})) == 0
    )


def test_drift_on_real_feature_distributions(container):
    ds = build_dataset(container.fetcher, 2024)
    X = ds.X[np.random.default_rng(7).permutation(len(ds.X))]  # rows are CIK-ordered (older filers first)
    half = len(X) // 2
    same = drift_report(X[:half], X[half:], FEATURES)
    assert same.overall in ("stable", "moderate") and not same.retrain_recommended
    shifted = X[half:].copy()
    shifted[:, 0] += 1.0
    shifted[:, 3] += 2.0
    rep = drift_report(X[:half], shifted, FEATURES)
    assert rep.overall == "significant" and rep.retrain_recommended
    assert psi(np.zeros(50), np.zeros(50)) == pytest.approx(0, abs=1e-6)
    assert [status(x) for x in (0.05, 0.2, 0.5)] == ["stable", "moderate", "significant"]
