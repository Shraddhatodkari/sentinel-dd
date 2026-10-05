import gzip
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from sentinel.db.models import IngestionRun, WatchlistEntry
from sentinel.eval.harness import FIXTURES
from sentinel.ingestion.reference import freshness
from sentinel.sources import watchlists as W
from sentinel.sources.catalog import OFAC_BASE, SOURCES
from sentinel.sources.replay import _key


def cassette(url: str) -> bytes:
    return gzip.decompress((FIXTURES / f"{_key(url)}.body.gz").read_bytes())


def meta(url: str) -> dict:
    return json.loads((FIXTURES / f"{_key(url)}.json").read_text())


def test_fixtures_are_documented_real_excerpts():
    for sid in ("ofac_sdn", "un_sc", "uk_sanctions", "eu_fsf", "us_congress"):
        m = meta(SOURCES[sid].url)
        assert m["excerpt"] is True and len(m["full_sha256"]) == 64 and m["selection"]


def test_parsers_on_real_list_excerpts():
    ofac = W.parse_ofac_sdn(cassette(SOURCES["ofac_sdn"].url), cassette(f"{OFAC_BASE}/alt.csv"))
    sber = next(r for r in ofac if r.primary_name == "PUBLIC JOINT STOCK COMPANY SBERBANK OF RUSSIA")
    assert "RUSSIA-EO14024" in sber.programs and {"scheme": "SWIFT/BIC", "value": "SABRRUMM"} in sber.identifiers
    assert any(n == "SBERBANK ROSSII" for n, _ in sber.names) and sber.entity_type == "entity"
    un = W.parse_un_consolidated(cassette(SOURCES["un_sc"].url))
    assert {r.entity_type for r in un} == {"individual", "entity"} and all(r.programs[0].startswith("UN") for r in un)
    uk = W.parse_uk_sanctions(cassette(SOURCES["uk_sanctions"].url))
    rus = next(r for r in uk if r.external_id == "RUS0256")
    assert "Sberbank" in [n for n, _ in rus.names] and rus.countries == ["Russia"]
    eu = W.parse_eu_fsf(cassette(SOURCES["eu_fsf"].url))
    assert any(n.lower() == "sberbank" for r in eu for n, _ in r.names)
    cong = W.parse_us_congress(cassette(SOURCES["us_congress"].url))
    assert all(r.category == "pep" and r.countries == ["US"] for r in cong)
    good, q = W.validate_records([*ofac, W.WatchlistRecord("x", "sanctions", "entity", "")])
    assert q["rejected_no_name"] == 1 and len(good) == len(ofac)


def test_parsers_reject_empty_inputs():
    with pytest.raises(W.ParseError):
        W.parse_ofac_sdn(b"")
    with pytest.raises(W.ParseError):
        W.parse_un_consolidated(b"<CONSOLIDATED_LIST/>")
    with pytest.raises(W.ParseError):
        W.parse_uk_sanctions(b"Report Date: x\nUnique ID,Name 6\n")
    with pytest.raises(W.ParseError):
        W.parse_eu_fsf(b"Entity_LogicalId;NameAlias_WholeName\n")
    with pytest.raises(W.ParseError):
        W.parse_us_congress(b"[]")
    os_rows = W.parse_opensanctions_simple(b"id,schema,name,aliases,countries\nQ1,Person,Jane Roe,J. Roe,us\n")
    assert os_rows[0].category == "pep" and len(os_rows[0].names) == 2


def test_refresh_is_idempotent_versioned_and_tracks_freshness(container):
    container.screening.ensure_loaded()
    before = container.screening.size
    run = container.reference.refresh("un_sc", "test")
    assert run.status == "succeeded" and run.unchanged and run.records_loaded == 0
    rec = container.audit.records(resource_id="un_sc")[0]
    assert rec["action"] == "source.refresh_succeeded" and rec["actor"] == "test" and rec["details"]["unchanged"]
    container.screening.ensure_loaded()
    assert container.screening.size == before
    fresh = {f["source_id"]: f for f in freshness(container.db)}
    assert fresh["ofac_sdn"]["current_records"] > 1000 and not fresh["ofac_sdn"]["stale"]
    assert fresh["opensanctions_peps"]["enabled"] is False and fresh["opensanctions_peps"]["stale"] is False
    assert fresh["sec_edgar"]["freshness_sla_h"] is None


def test_refresh_failure_keeps_previous_version(container, monkeypatch):
    with container.db.session() as s:
        n0 = s.scalar(
            select(func.count())
            .select_from(WatchlistEntry)
            .where(WatchlistEntry.source_id == "eu_fsf", WatchlistEntry.is_current.is_(True))
        )
    monkeypatch.setitem(W.PARSERS, "eu_fsf", lambda b: (_ for _ in ()).throw(W.ParseError("corrupt file")))
    import sentinel.ingestion.reference as ref

    monkeypatch.setitem(ref.PARSERS, "eu_fsf", W.PARSERS["eu_fsf"])
    r = container.reference.refresh("eu_fsf", "test")
    assert r.status == "failed" and "corrupt file" in r.error
    with container.db.session() as s:
        n1 = s.scalar(
            select(func.count())
            .select_from(WatchlistEntry)
            .where(WatchlistEntry.source_id == "eu_fsf", WatchlistEntry.is_current.is_(True))
        )
        last = s.scalars(
            select(IngestionRun).where(IngestionRun.source_id == "eu_fsf").order_by(IngestionRun.started_at.desc())
        ).first()
    assert n0 == n1 > 0 and last.status == "failed"
    failed = container.audit.records(resource_id="eu_fsf")
    assert failed and failed[0]["action"] == "source.refresh_failed" and "corrupt file" in failed[0]["details"]["error"]


def test_quality_gate_blocks_truncated_list(container, monkeypatch):
    import sentinel.ingestion.reference as ref

    real = W.PARSERS["uk_sanctions"]
    monkeypatch.setitem(ref.PARSERS, "uk_sanctions", lambda b: real(b)[:10])
    container.reference.fetcher.cache_ttl_s = 0
    with container.db.session() as s:  # make the stored version look different so a reload is attempted
        last = s.scalars(
            select(IngestionRun).where(IngestionRun.source_id == "uk_sanctions", IngestionRun.status == "succeeded")
        ).first()
        last.quality = {**last.quality, "version_key": "different"}
    r = container.reference.refresh("uk_sanctions", "test")
    assert r.status == "failed" and "quality gate" in r.error


def test_stale_list_detection(container):
    from datetime import timedelta

    from sentinel.db.models import SourceDocument
    from sentinel.timeutil import utcnow

    with container.db.session() as s:
        for d in s.scalars(select(SourceDocument).where(SourceDocument.source_id == "ofac_sdn")):
            d.retrieved_at = utcnow() - timedelta(hours=100)
    fresh = {f["source_id"]: f for f in freshness(container.db)}
    assert fresh["ofac_sdn"]["stale"] and fresh["ofac_sdn"]["age_hours"] >= 99


def test_fixture_dir_exists():
    assert Path(FIXTURES).is_dir()


def test_freshness_sla_is_configurable(workdir):
    from sentinel.eval.harness import offline_container
    from sentinel.ingestion.reference import freshness

    c = offline_container(workdir, sanctions_max_age_h=12, pep_max_age_h=48)
    sla = {f["source_id"]: f["freshness_sla_h"] for f in freshness(c.db)}
    assert sla["ofac_sdn"] == 12 and sla["us_congress"] == 48 and sla["sec_edgar"] is None
    c.close()


def test_llm_output_token_ceiling():
    from sentinel.llm.base import Message
    from sentinel.llm.extractive import ExtractiveLLM
    from sentinel.llm.metering import MeteredLLM

    seen = {}

    class Spy(ExtractiveLLM):
        def complete(self, messages, *, system=None, max_tokens=1024, temperature=0.0):
            seen["max_tokens"] = max_tokens
            return super().complete(messages, system=system, max_tokens=max_tokens, temperature=temperature)

    MeteredLLM(Spy(), None, {}, max_tokens_cap=256).complete([Message("user", "hello")], max_tokens=2048)
    assert seen["max_tokens"] == 256


def test_configuration_docs_cover_every_setting():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    res = subprocess.run([sys.executable, "scripts/gen_config_docs.py", "--check"], cwd=root, capture_output=True)
    assert res.returncode == 0, res.stderr.decode()
