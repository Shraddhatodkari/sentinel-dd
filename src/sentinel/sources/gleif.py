"""GLEIF Global LEI Index connector (Level 1 reference data + Level 2 accounting-consolidation parents/children).

Note: Level 2 relationships express *accounting consolidation* (IS_DIRECTLY/ULTIMATELY_CONSOLIDATED_BY), not
percentage ownership. Sentinel labels them as such and never converts them into ownership percentages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from sentinel.sources.edgar import norm_company
from sentinel.sources.fetcher import Fetched, SourceFetcher
from sentinel.sources.http import SourceNotFound

SRC = "gleif"
BASE = "https://api.gleif.org/api/v1"


@dataclass
class LeiRecord:
    lei: str
    legal_name: str
    other_names: list[str]
    jurisdiction: str | None
    legal_country: str | None
    hq_country: str | None
    entity_status: str
    registration_status: str
    category: str | None
    creation_date: str | None
    last_update: str | None
    corroboration: str | None
    document_id: str
    url: str
    score: float = 0.0
    raw_address: dict[str, Any] = field(default_factory=dict)


def _record(item: dict[str, Any], doc: Fetched) -> LeiRecord:
    a = item["attributes"]
    e = a["entity"]
    reg = a.get("registration", {})
    return LeiRecord(
        lei=item["id"],
        legal_name=e["legalName"]["name"],
        other_names=[o["name"] for o in (e.get("otherNames") or []) + (e.get("transliteratedOtherNames") or [])],
        jurisdiction=e.get("jurisdiction"),
        legal_country=(e.get("legalAddress") or {}).get("country"),
        hq_country=(e.get("headquartersAddress") or {}).get("country"),
        entity_status=e.get("status") or "",
        registration_status=reg.get("status") or "",
        category=e.get("category"),
        creation_date=e.get("creationDate"),
        last_update=reg.get("lastUpdateDate"),
        corroboration=reg.get("corroborationLevel"),
        document_id=doc.document_id,
        url=doc.url,
        raw_address=e.get("legalAddress") or {},
    )


class GleifConnector:
    def __init__(self, fetcher: SourceFetcher) -> None:
        self.f = fetcher

    def by_lei(self, lei: str) -> LeiRecord:
        doc = self.f.get(SRC, f"{BASE}/lei-records/{lei}")
        return _record(doc.json()["data"], doc)

    def search(self, name: str, limit: int = 5, variants: list[str] | None = None) -> list[LeiRecord]:
        """Exact legal-name filter first (precise), then full-text (recall), over all name variants."""
        names = [n for n in dict.fromkeys([name, *(variants or [])]) if n and n.strip()]
        nq = [norm_company(n) for n in names]
        found: dict[str, LeiRecord] = {}
        for n in names[:4]:
            for flt in ("filter[entity.legalName]", "filter[fulltext]"):
                doc = self.f.get(SRC, f"{BASE}/lei-records", {flt: n, "page[size]": 20})
                for item in doc.json().get("data", []):
                    rec = _record(item, doc)
                    cands = [rec.legal_name, *rec.other_names]
                    rec.score = round(
                        max(fuzz.token_sort_ratio(q, norm_company(c)) for q in nq for c in cands) / 100, 3
                    )
                    if rec.lei not in found or rec.score > found[rec.lei].score:
                        found[rec.lei] = rec
                if any(r.score >= 0.97 for r in found.values()):
                    break
            if any(r.score >= 0.97 for r in found.values()):
                break
        return sorted(found.values(), key=lambda r: (-r.score, r.registration_status != "ISSUED"))[:limit]

    def parent(self, lei: str, kind: str = "ultimate") -> LeiRecord | None:
        try:
            doc = self.f.get(SRC, f"{BASE}/lei-records/{lei}/{kind}-parent")
        except SourceNotFound:
            return None  # no reported parent (or reporting exception)
        data = doc.json().get("data")
        return _record(data, doc) if data else None

    def children(self, lei: str, limit: int = 50) -> tuple[list[LeiRecord], int]:
        try:
            doc = self.f.get(SRC, f"{BASE}/lei-records/{lei}/direct-children", {"page[size]": limit})
        except SourceNotFound:
            return [], 0
        d = doc.json()
        total = d.get("meta", {}).get("pagination", {}).get("total", 0)
        return [_record(x, doc) for x in d.get("data", [])], total
