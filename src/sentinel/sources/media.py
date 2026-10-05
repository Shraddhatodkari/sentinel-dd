"""Adverse-media and enforcement connectors.

* DOJ press releases (official U.S. enforcement announcements) — authoritative.
* GDELT DOC 2.0 — global news *metadata* (title, URL, outlet, date). Article bodies are not stored.

Mentions are evidence that a publication exists; Sentinel does not treat a news mention as a proven fact.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any

from sentinel.sources.fetcher import Fetched, SourceFetcher
from sentinel.timeutil import normalize_published

DOJ_URL = "https://www.justice.gov/api/v1/press_releases.json"
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_TAG = re.compile(r"<[^>]+>")


@dataclass
class MediaItem:
    source_id: str
    title: str
    url: str
    published: str
    outlet: str
    excerpt: str
    document_id: str
    retrieved_from: str


def _clean(s: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", s or "")).split())


class DojConnector:
    def __init__(self, fetcher: SourceFetcher) -> None:
        self.f = fetcher

    def search(self, term: str, limit: int = 25) -> list[MediaItem]:
        doc = self.f.get(
            "doj_press", DOJ_URL, {"parameters[title]": term, "pagesize": limit, "sort": "date", "direction": "DESC"}
        )
        out = []
        for r in doc.json().get("results", []) or []:
            body = _clean(r.get("body", ""))
            out.append(
                MediaItem(
                    "doj_press",
                    _clean(r.get("title", "")),
                    r.get("url", ""),
                    normalize_published(r.get("date", "")),
                    "U.S. Department of Justice" + (f" — {r['component'][0]['name']}" if r.get("component") else ""),
                    body[:600],
                    doc.document_id,
                    doc.url,
                )
            )
        return out


class GdeltConnector:
    def __init__(self, fetcher: SourceFetcher, timespan: str = "1week") -> None:
        self.f = fetcher
        self.timespan = timespan

    def search(self, term: str, limit: int = 75, timespan: str | None = None) -> list[MediaItem]:
        params: dict[str, Any] = {
            "query": f'"{term}" sourcelang:english',
            "mode": "artlist",
            "format": "json",
            "maxrecords": limit,
            "sort": "datedesc",
            "timespan": timespan or self.timespan,
        }
        return self.parse(self.f.get("gdelt", GDELT_URL, params))

    @staticmethod
    def parse(doc: Fetched) -> list[MediaItem]:
        """Parse a DOC 2.0 ``artlist`` JSON response. ``{}`` is GDELT's "no results"; a non-JSON body (e.g. its
        plain-text rate-limit notice served with HTTP 200 or 429) is an error, never an empty result."""
        if not doc.content.strip().startswith(b"{"):
            raise ValueError(f"GDELT returned a non-JSON response: {doc.text[:120]!r}")
        out = []
        for a in doc.json().get("articles", []) or []:
            out.append(
                MediaItem(
                    "gdelt",
                    _clean(a.get("title", "")),
                    a.get("url", ""),
                    normalize_published(a.get("seendate", "")),
                    a.get("domain", ""),
                    "",
                    doc.document_id,
                    doc.url,
                )
            )
        return out
