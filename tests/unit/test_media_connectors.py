"""Connector format tests. GDELT could not be recorded (the shared CI egress IP is rate-limited with HTTP 429 —
see docs/data_sources.md), so the parser is checked against the documented DOC 2.0 ``artlist`` schema using a
clearly synthetic payload. The real 429 body is also replayed to prove it is treated as unavailable."""

import json

import httpx
import pytest

from sentinel.db.base import Database
from sentinel.ingestion.reference import sync_source_catalog
from sentinel.sources.fetcher import SourceFetcher
from sentinel.sources.http import ResilientHttpClient
from sentinel.sources.media import DojConnector, GdeltConnector
from sentinel.sources.raw_store import RawStore

SYNTHETIC_ARTLIST = {
    "articles": [
        {
            "url": "https://news.example/a",
            "title": "EXAMPLE Corp &amp; partner announce plan",
            "seendate": "20260101T000000Z",
            "domain": "news.example",
            "language": "English",
        }
    ]
}
REAL_429_BODY = b"Please limit requests to one every 5 seconds or contact kalev.leetaru5@gmail.com for larger queries."


def _fetcher(tmp_path, handler):
    db = Database(f"sqlite:///{tmp_path}/m.db")
    db.upgrade()
    sync_source_catalog(db)
    http = ResilientHttpClient("ua", transport=httpx.MockTransport(handler), max_retries=0, rate_limits_enabled=False)
    return SourceFetcher(db, http, RawStore(tmp_path / "raw"), cache_ttl_s=0)


def test_gdelt_artlist_schema_and_html_unescape(tmp_path):
    f = _fetcher(tmp_path, lambda r: httpx.Response(200, content=json.dumps(SYNTHETIC_ARTLIST).encode()))
    items = GdeltConnector(f).search("EXAMPLE Corp")
    assert items[0].title == "EXAMPLE Corp & partner announce plan" and items[0].outlet == "news.example"


def test_gdelt_non_json_rate_limit_body_is_an_error(tmp_path):
    f = _fetcher(tmp_path, lambda r: httpx.Response(200, content=REAL_429_BODY))
    with pytest.raises(ValueError, match="non-JSON"):
        GdeltConnector(f).search("x")


def test_doj_strips_html(tmp_path):
    body = {
        "results": [
            {
                "title": "Company &amp; Co Charged",
                "url": "https://www.justice.gov/x",
                "date": "1",
                "body": "<p>Text</p>",
                "component": [{"name": "Criminal Division"}],
            }
        ]
    }
    f = _fetcher(tmp_path, lambda r: httpx.Response(200, content=json.dumps(body).encode()))
    it = DojConnector(f).search("Company")[0]
    assert it.title == "Company & Co Charged" and it.excerpt == "Text" and "Criminal Division" in it.outlet


def test_publication_dates_are_normalised_to_iso():
    from sentinel.timeutil import normalize_published

    assert normalize_published("1707739200") == "2024-02-12T12:00:00+00:00"  # DOJ epoch seconds
    assert normalize_published(1707739200) == "2024-02-12T12:00:00+00:00"
    assert normalize_published("20260103T141500Z") == "2026-01-03T14:15:00+00:00"  # GDELT seendate
    assert normalize_published("20261399T999999Z") == "20261399T999999Z"  # invalid: returned unchanged
    assert normalize_published("") == "" and normalize_published(None) == ""
