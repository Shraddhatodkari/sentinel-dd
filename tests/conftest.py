"""Shared fixtures. Everything runs offline & deterministically (FakeLLM, hashing embeddings)."""

from __future__ import annotations

import pytest

from sentinel.config import Settings
from sentinel.container import Container
from sentinel.llm.fake import FakeLLM
from sentinel.ml.models import TrainedBundle, train_all
from sentinel.observability.metrics import METRICS


@pytest.fixture(scope="session")
def bundle() -> TrainedBundle:
    b, _ = train_all(n=2000, seed=7)
    return b


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(_env_file=None, env="test", llm_provider="fake", rate_limit_per_minute=10_000)


@pytest.fixture
def container(settings: Settings, bundle: TrainedBundle) -> Container:
    return Container.build(settings, llm=FakeLLM(), bundle=bundle)


@pytest.fixture
def empty_container(settings: Settings, bundle: TrainedBundle) -> Container:
    return Container.build(settings, llm=FakeLLM(), bundle=bundle, bootstrap_data=False)


@pytest.fixture(autouse=True)
def _reset_metrics():
    METRICS.reset()
    yield
