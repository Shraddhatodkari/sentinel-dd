"""File-based model registry with versioning, stages, integrity hashes and optional MLflow mirroring.

Layout::

    <root>/<name>/v<N>/model.pkl
    <root>/<name>/v<N>/meta.json   # metrics, params, features, sha256, created_at, stage
"""

from __future__ import annotations

import hashlib
import json
import pickle
import time
from pathlib import Path
from typing import Any

STAGES = ("none", "staging", "production", "archived")


class RegistryError(Exception):
    pass


class ModelRegistry:
    def __init__(self, root: str | Path, mlflow_uri: str | None = None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.mlflow_uri = mlflow_uri

    def _dir(self, name: str, version: int) -> Path:
        return self.root / name / f"v{version}"

    def versions(self, name: str) -> list[int]:
        d = self.root / name
        if not d.exists():
            return []
        return sorted(int(p.name[1:]) for p in d.iterdir() if p.is_dir() and p.name.startswith("v"))

    def meta(self, name: str, version: int) -> dict[str, Any]:
        return json.loads((self._dir(name, version) / "meta.json").read_text())

    def register(
        self,
        name: str,
        model: Any,
        metrics: dict[str, float],
        params: dict[str, Any] | None = None,
        features: list[str] | None = None,
    ) -> int:
        version = (self.versions(name) or [0])[-1] + 1
        d = self._dir(name, version)
        d.mkdir(parents=True)
        blob = pickle.dumps(model)
        (d / "model.pkl").write_bytes(blob)
        meta = {
            "name": name,
            "version": version,
            "metrics": metrics,
            "params": params or {},
            "features": features or [],
            "sha256": hashlib.sha256(blob).hexdigest(),
            "created_at": time.time(),
            "stage": "none",
        }
        (d / "meta.json").write_text(json.dumps(meta, indent=2))
        self._mirror_mlflow(name, version, metrics, params or {})
        return version

    def _mirror_mlflow(self, name: str, version: int, metrics: dict[str, float], params: dict[str, Any]) -> None:
        if not self.mlflow_uri:
            return
        try:  # pragma: no cover - optional integration
            import mlflow

            mlflow.set_tracking_uri(self.mlflow_uri)
            mlflow.set_experiment(f"sentinel-{name}")
            with mlflow.start_run(run_name=f"{name}-v{version}"):
                mlflow.log_params({k: str(v) for k, v in params.items()})
                mlflow.log_metrics(metrics)
        except Exception:  # noqa: S110 - tracking must never break training
            pass

    def set_stage(self, name: str, version: int, stage: str) -> None:
        if stage not in STAGES:
            raise RegistryError(f"invalid stage {stage}")
        if stage == "production":  # single production version: archive the previous one
            for v in self.versions(name):
                m = self.meta(name, v)
                if m["stage"] == "production" and v != version:
                    m["stage"] = "archived"
                    (self._dir(name, v) / "meta.json").write_text(json.dumps(m, indent=2))
        m = self.meta(name, version)
        m["stage"] = stage
        (self._dir(name, version) / "meta.json").write_text(json.dumps(m, indent=2))

    def latest(self, name: str, stage: str | None = None) -> int | None:
        for v in reversed(self.versions(name)):
            if stage is None or self.meta(name, v)["stage"] == stage:
                return v
        return None

    def load(self, name: str, version: int | None = None, stage: str = "production") -> tuple[Any, dict[str, Any]]:
        v = version or self.latest(name, stage)
        if v is None:
            raise RegistryError(f"no {stage} version of {name}")
        d = self._dir(name, v)
        blob = (d / "model.pkl").read_bytes()
        meta = self.meta(name, v)
        if hashlib.sha256(blob).hexdigest() != meta["sha256"]:
            raise RegistryError(f"integrity check failed for {name} v{v} — artifact tampered")
        # Safe: artifact produced by our own pipeline and integrity-verified above.
        return pickle.loads(blob), meta  # noqa: S301  # nosec B301
