"""Structural checks of deployment artefacts (they are not applied to a cluster in CI)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sentinel import __version__

yaml = pytest.importorskip("yaml")
ROOT = Path(__file__).resolve().parents[2]


def _docs(path: str) -> list[dict]:
    return [d for d in yaml.safe_load_all((ROOT / path).read_text()) if d]


def _containers(doc: dict) -> list[dict]:
    spec = doc["spec"]
    if doc["kind"] == "CronJob":
        spec = spec["jobTemplate"]["spec"]
    return spec["template"]["spec"]["containers"]


def test_kubernetes_manifests():
    docs = _docs("infra/k8s/deployment.yaml") + _docs("infra/k8s/jobs.yaml") + _docs("infra/k8s/configmap.yaml")
    kinds = {d["kind"] for d in docs}
    assert kinds >= {"Deployment", "Service", "HorizontalPodAutoscaler", "PodDisruptionBudget", "NetworkPolicy"}
    assert kinds >= {"Job", "CronJob", "PersistentVolumeClaim", "ConfigMap", "Secret"}
    for d in docs:
        assert d.get("apiVersion") and d.get("metadata", {}).get("name"), d
        if d["kind"] in ("Deployment", "Job", "CronJob"):
            for c in _containers(d):
                assert c["image"].endswith(f":{__version__}"), c["image"]
                sc = c["securityContext"]
                assert sc["allowPrivilegeEscalation"] is False and sc["readOnlyRootFilesystem"] is True
                assert sc["capabilities"]["drop"] == ["ALL"]
    dep = next(d for d in docs if d["kind"] == "Deployment")
    pod = dep["spec"]["template"]["spec"]
    assert pod["securityContext"]["runAsNonRoot"] is True
    api = pod["containers"][0]
    assert api["args"] == ["serve"]
    assert api["readinessProbe"]["httpGet"]["path"] == "/ready" and api["livenessProbe"]["httpGet"]["path"] == "/health"
    job = next(d for d in docs if d["kind"] == "Job")
    assert _containers(job)[0]["args"] == ["db", "upgrade"]
    cron = next(d for d in docs if d["kind"] == "CronJob")
    assert _containers(cron)[0]["args"] == ["sources", "refresh", "--all"]
    cm = next(d for d in docs if d["kind"] == "ConfigMap")["data"]
    from sentinel.config import Settings

    known = {f"SENTINEL_{k.upper()}" for k in Settings.model_fields}
    assert set(cm) <= known, set(cm) - known  # no stale setting names
    secret = next(d for d in docs if d["kind"] == "Secret")["stringData"]
    # template only: every non-empty value must still contain a <placeholder>
    assert set(secret) <= known and all(v == "" or "<" in v for v in secret.values())


def test_compose_ci_and_image():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    api = compose["services"]["api"]
    assert api["image"] == f"sentinel-dd:{__version__}" and api["read_only"] is True and api["cap_drop"] == ["ALL"]
    assert api["depends_on"]["postgres"]["condition"] == "service_healthy"
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    assert {"backend-quality", "dashboard", "test", "eval-gate", "security", "docker"} <= set(ci["jobs"])
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "USER 10001:10001" in dockerfile and "SENTINEL_ENV=prod" in dockerfile and "HEALTHCHECK" in dockerfile
    entry = (ROOT / "infra/docker/entrypoint.sh").read_text()
    assert "sentinel db upgrade" in entry and "SENTINEL_MIGRATE_ON_START" in entry


def test_env_example_names_are_real_settings():
    from sentinel.config import Settings

    known = {f"SENTINEL_{k.upper()}" for k in Settings.model_fields}
    names = {
        line.split("=", 1)[0]
        for line in (ROOT / ".env.example").read_text().splitlines()
        if line.startswith("SENTINEL_")
    }
    assert names and names <= known, names - known


def test_versions_are_consistent():
    import json
    import tomllib

    assert tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"] == __version__
    assert json.loads((ROOT / "dashboard/package.json").read_text())["version"] == __version__
    assert f"## [{__version__}]" in (ROOT / "CHANGELOG.md").read_text()
