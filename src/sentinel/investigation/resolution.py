"""Entity resolution across SEC EDGAR and GLEIF.

Strategy (deterministic, explainable):
1. Explicit identifiers (CIK / LEI / ticker) win.
2. Otherwise name search in EDGAR (ticker map) and GLEIF (full-text) with RapidFuzz scoring.
3. Cross-source linkage: EDGAR-reported LEI, else GLEIF/EDGAR name agreement ≥ 0.9.
4. Ambiguity (two strong but different candidates) is flagged and lowers confidence → policy forces review.

The resolved entity and its identifiers are persisted (``entities`` / ``entity_identifiers``) for re-use.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import select

from sentinel.db.base import Database
from sentinel.db.models import Entity, EntityIdentifier
from sentinel.sources.edgar import EdgarCandidate, EdgarConnector, EdgarProfile, name_variants, norm_company
from sentinel.sources.gleif import GleifConnector, LeiRecord
from sentinel.sources.http import SourceError


@dataclass
class Resolution:
    query: str
    name: str | None
    entity_type: str = "company"
    cik: int | None = None
    lei: str | None = None
    tickers: list[str] = field(default_factory=list)
    jurisdiction: str | None = None
    country: str | None = None
    confidence: float = 0.0
    method: str = "unresolved"
    ambiguous: bool = False
    aliases: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    entity_id: str | None = None

    @property
    def resolved(self) -> bool:
        return self.name is not None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EntityResolver:
    def __init__(self, db: Database, edgar: EdgarConnector, gleif: GleifConnector) -> None:
        self.db = db
        self.edgar = edgar
        self.gleif = gleif

    def resolve(
        self, query: str, cik: int | None = None, lei: str | None = None, ticker: str | None = None
    ) -> tuple[Resolution, EdgarProfile | None, LeiRecord | None]:
        res = Resolution(query=query, name=None)
        profile: EdgarProfile | None = None
        lei_rec: LeiRecord | None = None
        edgar_cands: list[EdgarCandidate] = []
        gleif_cands: list[LeiRecord] = []

        # ---- EDGAR
        try:
            if cik:
                profile = self.edgar.profile(cik)
                res.method = "cik"
            else:
                edgar_cands = self.edgar.search(ticker or query)
                if edgar_cands and edgar_cands[0].score >= 0.9:
                    profile = self.edgar.profile(edgar_cands[0].cik)
                    res.method = f"edgar_{edgar_cands[0].method}"
        except SourceError as exc:
            res.errors.append(f"sec_edgar: {exc}")

        # ---- GLEIF
        try:
            target_lei = lei or (profile.lei if profile and profile.lei else None)
            if target_lei:
                lei_rec = self.gleif.by_lei(target_lei)
                lei_rec.score = 1.0
                if res.method == "unresolved":
                    res.method = "lei"
            else:
                gleif_cands = self.gleif.search(
                    profile.name if profile else query, variants=[*name_variants(query), query]
                )
                if gleif_cands:
                    best = gleif_cands[0]
                    if profile is not None:
                        agree = fuzz.token_sort_ratio(norm_company(profile.name), norm_company(best.legal_name)) / 100
                        us_like = (best.legal_country or "") == "US"
                        if best.score >= 0.9 and (agree >= 0.9 or best.score >= 0.97) and us_like:
                            lei_rec = best
                    elif best.score >= 0.9:
                        lei_rec = best
                        res.method = "gleif_name"
        except SourceError as exc:
            res.errors.append(f"gleif: {exc}")

        res.candidates = [
            {"source": "sec_edgar", "cik": c.cik, "name": c.name, "ticker": c.ticker, "score": c.score}
            for c in edgar_cands[:5]
        ] + [
            {
                "source": "gleif",
                "lei": g.lei,
                "name": g.legal_name,
                "country": g.legal_country,
                "score": g.score,
                "registration_status": g.registration_status,
            }
            for g in gleif_cands[:5]
        ]

        # ---- consolidate
        if profile is not None:
            res.name, res.cik, res.tickers = profile.name, profile.cik, profile.tickers
            res.jurisdiction = profile.state_of_incorporation or None
            res.country = "US" if not lei_rec else (lei_rec.legal_country or "US")
            top = (
                1.0
                if res.method in ("cik", "edgar_ticker", "edgar_cik")
                else (edgar_cands[0].score if edgar_cands else 0.9)
            )
            res.confidence = top
            strong = [c for c in edgar_cands if c.score >= 0.9]
            if len(strong) > 1 and strong[0].score - strong[1].score < 0.03 and res.method == "edgar_name":
                res.ambiguous = True
                res.confidence = min(res.confidence, 0.7)
        elif lei_rec is not None:
            res.name = lei_rec.legal_name
            res.jurisdiction, res.country = lei_rec.jurisdiction, lei_rec.legal_country
            res.confidence = lei_rec.score
            strong_g = [g for g in gleif_cands if g.score >= 0.9]
            if len(strong_g) > 1 and strong_g[0].score - strong_g[1].score < 0.03 and not lei:
                res.ambiguous = True
                res.confidence = min(res.confidence, 0.7)
        if lei_rec is not None:
            res.lei = lei_rec.lei
            if profile is not None and profile.lei and profile.lei == lei_rec.lei:
                res.confidence = min(1.0, res.confidence + 0.05)  # independently corroborated identifier
        aliases: list[str] = []
        if lei_rec is not None:
            aliases += [lei_rec.legal_name, *lei_rec.other_names]
        if profile is not None:
            aliases += [f["name"] for f in profile.former_names]
        res.aliases = [a for a in dict.fromkeys(aliases) if a and a != res.name][:20]
        if res.resolved:
            res.entity_id = self._persist(res, lei_rec)
        return res, profile, lei_rec

    def _persist(self, res: Resolution, lei_rec: LeiRecord | None) -> str:
        idents = [("CIK", str(res.cik)) if res.cik else None, ("LEI", res.lei) if res.lei else None]
        idents += [("TICKER", t) for t in res.tickers]
        pairs = [p for p in idents if p]
        with self.db.session() as s:
            entity = None
            for scheme, value in pairs:
                row = s.scalars(
                    select(EntityIdentifier).where(EntityIdentifier.scheme == scheme, EntityIdentifier.value == value)
                ).first()
                if row is not None:
                    entity = s.get(Entity, row.entity_id)
                    break
            if entity is None:
                entity = Entity(
                    canonical_name=res.name or res.query, entity_type="company", jurisdiction=res.jurisdiction
                )
                s.add(entity)
                s.flush()
            entity.canonical_name = res.name or entity.canonical_name
            entity.jurisdiction = res.jurisdiction or entity.jurisdiction
            entity.attributes = {
                **(entity.attributes or {}),
                "country": res.country,
                "other_names": lei_rec.other_names if lei_rec else [],
            }
            have = {(i.scheme, i.value) for i in entity.identifiers}
            for scheme, value in pairs:
                if (scheme, value) in have:
                    continue
                clash = s.scalars(
                    select(EntityIdentifier).where(EntityIdentifier.scheme == scheme, EntityIdentifier.value == value)
                ).first()
                if clash is None:
                    entity.identifiers.append(EntityIdentifier(scheme=scheme, value=value))
            return entity.id
