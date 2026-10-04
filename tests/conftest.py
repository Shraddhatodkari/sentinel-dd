"""Shared fixtures.

All tests run offline and deterministically against *recorded real source data* (tests/fixtures/http) via the
replay transport. A seeded workspace (migrated DB, official-list excerpts loaded, peer model trained on recorded
SEC frames, users created) is built once per session and copied for each test that needs isolation.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from sentinel.eval.harness import FIXTURES, offline_container
from sentinel.observability.metrics import METRICS
from sentinel.security.auth import Principal

PASSWORD = "Correct-Horse-9!x"
USERS = {
    "alice": ["analyst"],
    "mallory": ["analyst"],
    "bob": ["reviewer"],
    "sara": ["senior_reviewer"],
    "carol": ["auditor"],
    "root": ["admin"],
}


@pytest.fixture(scope="session")
def seeded_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("seed")
    c = offline_container(d)
    runs = c.reference.refresh_all("test-seed")
    assert all(r.status == "succeeded" for r in runs), [r.error for r in runs]
    from sentinel.ml.train import run as train_peer
    from sentinel.security.users import UserService

    assert train_peer(c.fetcher, c.registry, 2024).promoted
    us = UserService(c.db)
    for u, roles in USERS.items():
        us.create(u, PASSWORD, roles, display_name=u.title())
    us.create("eve", PASSWORD, ["analyst"], tenant="other-bank", display_name="Eve")
    c.checkpointer.conn.close()
    c.db.engine.dispose()
    return d


@pytest.fixture
def workdir(seeded_dir: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "ws"
    shutil.copytree(seeded_dir, dst)
    return dst


@pytest.fixture
def container(workdir: Path):
    c = offline_container(workdir, FIXTURES)
    yield c
    c.db.engine.dispose()


@pytest.fixture
def service(container):
    from sentinel.agents.service import CaseService

    s = CaseService(container)
    yield s
    s.shutdown()


@pytest.fixture
def principal(container):
    from sentinel.security.users import UserService

    us = UserService(container.db)

    def get(name: str) -> Principal:
        p = us.get_principal(name)
        assert p is not None
        return p

    return get


@pytest.fixture(autouse=True)
def _reset_metrics():
    METRICS.reset()
    yield
