import gzip
from datetime import UTC, datetime

import httpx
import pytest

from sentinel.db.base import Database
from sentinel.reliability import CircuitBreaker, MinIntervalLimiter
from sentinel.sources.fetcher import SourceFetcher
from sentinel.sources.http import ResilientHttpClient, SourceNotFound, SourceUnavailable
from sentinel.sources.raw_store import RawStore
from sentinel.sources.replay import (
    RecordingTransport,
    ReplayTransport,
    UnrecordedRequest,
    canonical_url,
    write_cassette,
)


def _client(handler, **kw):
    return ResilientHttpClient(
        "ua test@example.org",
        transport=httpx.MockTransport(handler),
        backoff_base_s=0.0,
        rate_limits_enabled=False,
        **kw,
    )


def test_retry_then_success_and_retry_after(monkeypatch):
    calls = {"n": 0}
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    def h(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503 if calls["n"] == 1 else 429, headers={"retry-after": "2"})
        return httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"})

    res = _client(h).get("https://example.org/x")
    assert res.json() == {"ok": True} and calls["n"] == 3 and 2.0 in sleeps


def test_not_found_non_retryable_exhausted_and_circuit(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(SourceNotFound):
        _client(lambda r: httpx.Response(404)).get("https://example.org/a")
    with pytest.raises(SourceUnavailable, match="non-retryable"):
        _client(lambda r: httpx.Response(403)).get("https://example.org/b")
    c = _client(lambda r: httpx.Response(503), max_retries=1, breaker_threshold=2)
    for _ in range(2):
        with pytest.raises(SourceUnavailable, match="retries exhausted"):
            c.get("https://down.example/x")
    with pytest.raises(SourceUnavailable, match="circuit open"):
        c.get("https://down.example/y")
    assert c.breaker_states()["down.example"] == "open"

    def boom(r):
        raise httpx.ConnectTimeout("t")

    with pytest.raises(SourceUnavailable, match="ConnectTimeout"):
        _client(boom, max_retries=0).get("https://slow.example/x")


def test_breaker_and_limiter_primitives():
    cb = CircuitBreaker(failure_threshold=1, reset_after_s=0.0)
    cb.record_failure()
    assert cb.state == "half-open" and cb.allow()
    cb.record_success()
    assert cb.state == "closed"
    lim = MinIntervalLimiter()
    assert lim.wait("k", 0) == 0.0 and lim.wait("h", 0.01) == 0.0 and lim.wait("h", 0.01) > 0


def test_raw_store_is_content_addressed_and_verified(tmp_path):
    rs = RawStore(tmp_path)
    sha, path = rs.put(b"hello")
    assert rs.put(b"hello") == (sha, path) and rs.get(sha) == b"hello" and rs.exists(sha)
    (tmp_path / path).write_bytes(gzip.compress(b"tampered"))
    with pytest.raises(ValueError, match="integrity"):
        rs.get(sha)


def test_fetcher_lineage_cache_and_idempotency(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/f.db")
    db.upgrade()
    from sentinel.ingestion.reference import sync_source_catalog

    sync_source_catalog(db)
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        return httpx.Response(200, content=b'{"v": 1}', headers={"content-type": "application/json"})

    f = SourceFetcher(db, _client(h), RawStore(tmp_path / "raw"), cache_ttl_s=3600)
    a = f.get("sec_edgar", "https://data.sec.gov/x", {"b": 2, "a": 1})
    b = f.get("sec_edgar", "https://data.sec.gov/x", {"a": 1, "b": 2})
    assert calls["n"] == 1 and b.from_cache and a.document_id == b.document_id and a.url.endswith("?a=1&b=2")
    c = f.get("sec_edgar", "https://data.sec.gov/x", {"a": 1, "b": 2}, use_cache=False)
    assert calls["n"] == 2 and c.document_id == a.document_id  # identical bytes → same document row
    assert c.json() == {"v": 1} and c.text == '{"v": 1}' and a.retrieved_at.tzinfo is not None


def test_record_and_replay(tmp_path):
    inner = httpx.MockTransport(lambda r: httpx.Response(200, content=b"body", headers={"content-type": "text/plain"}))
    rec = ResilientHttpClient("ua", transport=RecordingTransport(tmp_path, inner), rate_limits_enabled=False)
    assert rec.get("https://example.org/p?b=1&a=2").content == b"body"
    rep = ResilientHttpClient("ua", transport=ReplayTransport(tmp_path), rate_limits_enabled=False)
    assert rep.get("https://example.org/p?a=2&b=1").content == b"body"
    with pytest.raises(UnrecordedRequest):
        rep.get("https://example.org/other")
    lenient = ResilientHttpClient(
        "ua", transport=ReplayTransport(tmp_path, strict=False), max_retries=0, rate_limits_enabled=False
    )
    with pytest.raises(SourceUnavailable):
        lenient.get("https://example.org/other")
    write_cassette(tmp_path, "https://example.org/excerpt", b"x", "text/plain", excerpt=True)
    assert rep.get("https://example.org/excerpt").content == b"x"
    assert canonical_url("HTTPS://Example.org/p?b=1&a=2#frag") == "https://example.org/p?a=2&b=1"
    assert datetime.now(UTC)
