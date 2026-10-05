"""Operations CLI against an offline (recorded real data) workspace."""

from __future__ import annotations

import json

import pytest

from sentinel import cli
from sentinel.eval.harness import FIXTURES, offline_container


@pytest.fixture
def run(workdir, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_container", lambda: offline_container(workdir, FIXTURES))
    monkeypatch.setenv("SENTINEL_DATABASE_URL", f"sqlite:///{workdir}/sentinel.db")
    from sentinel.config import get_settings

    get_settings.cache_clear()

    def _run(*argv: str) -> tuple[int, object]:
        code = cli.main(list(argv))
        out = capsys.readouterr().out
        return code, json.loads(out) if out.strip() else None

    yield _run
    get_settings.cache_clear()


def test_db_users_sources_audit(run, monkeypatch):
    code, out = run("db", "current")
    assert code == 0 and out["current"] == out["head"]
    monkeypatch.setenv("SENTINEL_NEW_PASSWORD", "Another-Strong-Pass-1")
    code, out = run("users", "create", "--username", "dora", "--roles", "reviewer", "--display-name", "Dora")
    assert code == 0 and out["roles"] == ["reviewer"]
    code, _ = run("users", "create", "--username", "dora", "--roles", "reviewer")
    assert code == 2  # duplicate
    code, out = run("users", "list")
    assert any(u["username"] == "dora" for u in out)
    code, out = run("users", "token", "--username", "dora", "--ttl-minutes", "5")
    assert code == 0 and out["token"].count(".") == 2
    assert run("users", "token", "--username", "nobody")[0] == 2
    code, out = run("sources", "status")
    assert code == 0 and {s["source_id"] for s in out} >= {"ofac_sdn", "sec_edgar"}
    code, out = run("audit", "verify")
    assert code == 0 and out["intact"]


def test_investigate_and_drift(run):
    code, out = run("investigate", "Sberbank", "--as", "alice")
    assert code == 0 and out["risk_band"] == "HIGH" and "TPRM-SAN-01" in out["policy_rules"]
    assert run("investigate", "Sberbank", "--as", "ghost")[0] == 2
    code, out = run("ml", "drift", "--year", "2024")  # same period as the production model → stable
    assert code == 0 and out["overall"] == "stable" and not out["retrain_recommended"]


def test_screening_eval_against_loaded_lists(run):
    code, out = run("eval", "--screening-live")
    assert code == 0 and out["metrics"]["screening_recall"] >= 0.95 and not out["failures"]


def test_source_failure_is_reported_without_traceback(run, capsys):
    code, _ = run("ml", "drift", "--year", "1990")  # frames for 1990 were never recorded → upstream unavailable
    assert code == 1
