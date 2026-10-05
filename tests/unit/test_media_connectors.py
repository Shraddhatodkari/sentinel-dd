"""Media connector format tests on recorded real responses only.

GDELT throttles shared egress IPs (HTTP 429 / timeouts, see docs/data_sources.md); a successful real ``artlist``
response was recorded separately (``recorded_with: curl``) and is replayed here, together with GDELT's real
rate-limit notice and its real "no results" body (``{}``)."""

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from sentinel.sources.fetcher import Fetched
from sentinel.sources.media import DojConnector, GdeltConnector
from sentinel.sources.replay import ReplayTransport

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "http"
GDELT_RECORDED = (
    "https://api.gdeltproject.org/api/v2/doc/doc?format=json&maxrecords=25&mode=artlist&query=boeing&timespan=1week"
)
# body GDELT returned with HTTP 429 to this environment (verbatim prefix)
REAL_429_BODY = b"Please limit requests to one every 5 seconds or contact kalev.leetaru5@gmail.com for larger queries."


def _replayed(url: str) -> Fetched:
    resp = ReplayTransport(FIXTURES).handle_request(httpx.Request("GET", url))
    resp.read()
    return Fetched("doc-1", "gdelt", url, datetime.now(UTC), "0" * 64, resp.content, resp.headers["content-type"])


def test_gdelt_parses_recorded_real_artlist():
    items = GdeltConnector.parse(_replayed(GDELT_RECORDED))
    assert len(items) == 25 and all(i.source_id == "gdelt" and i.url.startswith("http") for i in items)
    assert all(i.published[:4] == "2026" and i.published.endswith("+00:00") for i in items)  # ISO-normalised
    assert all("&amp;" not in i.title for i in items) and sum("boeing" in i.title.lower() for i in items) >= 5
    assert all(i.document_id == "doc-1" and i.excerpt == "" for i in items)  # metadata only, no article text


def test_gdelt_no_results_and_rate_limit_bodies():
    empty = Fetched("d", "gdelt", "u", datetime.now(UTC), "0" * 64, b"{}", "application/json")
    assert GdeltConnector.parse(empty) == []
    notice = Fetched("d", "gdelt", "u", datetime.now(UTC), "0" * 64, REAL_429_BODY, "text/html")
    with pytest.raises(ValueError, match="non-JSON"):
        GdeltConnector.parse(notice)


def test_doj_strips_html_from_recorded_release(container):
    items = DojConnector(container.fetcher).search("boeing")
    raw = next(
        json.loads(gzip.decompress((FIXTURES / f.name.replace(".json", ".body.gz")).read_bytes()))
        for f in FIXTURES.glob("*.json")
        if "parameters%5Btitle%5D=boeing" in json.loads(f.read_text())["url"]
    )["results"]
    assert len(items) == len(raw) == 25
    assert any("<p" in (r.get("body") or "") for r in raw)  # the real API returns HTML bodies …
    assert all("<" not in i.excerpt and "&nbsp;" not in i.excerpt for i in items)  # … which are stripped
    assert all(i.outlet.startswith("U.S. Department of Justice") for i in items)
    assert all(i.published[:2] in ("19", "20") for i in items)  # epoch seconds normalised to ISO


def test_publication_dates_are_normalised_to_iso():
    from sentinel.timeutil import normalize_published

    assert normalize_published("1707739200") == "2024-02-12T12:00:00+00:00"  # DOJ epoch seconds
    assert normalize_published(1707739200) == "2024-02-12T12:00:00+00:00"
    assert normalize_published("20260103T141500Z") == "2026-01-03T14:15:00+00:00"  # GDELT seendate
    assert normalize_published("20261399T999999Z") == "20261399T999999Z"  # invalid: returned unchanged
    assert normalize_published("") == "" and normalize_published(None) == ""


def test_gdelt_query_uses_configured_window_and_short_name(tmp_path, make_db):
    from sentinel.ingestion.reference import sync_source_catalog
    from sentinel.sources.fetcher import SourceFetcher
    from sentinel.sources.http import ResilientHttpClient
    from sentinel.sources.raw_store import RawStore

    body = gzip.decompress(
        next(
            (FIXTURES / f.name.replace(".json", ".body.gz")).read_bytes()
            for f in FIXTURES.glob("*.json")
            if json.loads(f.read_text())["url"] == GDELT_RECORDED
        )
    )
    seen: list[httpx.URL] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url)
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    db = make_db("g.db")
    sync_source_catalog(db)
    http = ResilientHttpClient("ua", transport=httpx.MockTransport(handler), max_retries=0, rate_limits_enabled=False)
    gd = GdeltConnector(SourceFetcher(db, http, RawStore(tmp_path / "raw"), cache_ttl_s=0), timespan="1week")
    items = gd.search("boeing")
    assert len(items) == 25 and seen[0].params["timespan"] == "1week"
    assert seen[0].params["query"] == '"boeing" sourcelang:english' and seen[0].params["mode"] == "artlist"
