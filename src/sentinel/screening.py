"""Watch-list screening (sanctions + PEP) against the *current* persisted list versions.

Matching pipeline:
  normalise (Unicode folding, punctuation, legal-form stripping) → candidate blocking via an inverted token
  index (rare tokens only) → RapidFuzz scoring with subset bonus, alias-strength and entity-type penalties →
  identifier matching (LEI / SWIFT / registration numbers) which overrides name scores.

A hit is a *potential match* for human adjudication — never an automated determination.
"""

from __future__ import annotations

import math
import re
import threading
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import func, select

from sentinel.db.base import Database
from sentinel.db.models import IngestionRun, SourceDocument, WatchlistEntry, WatchlistName
from sentinel.timeutil import iso

SCREENING_VERSION = "screening:rapidfuzz-v2"  # v2: IDF-weighted organisation matching, distinctive-token check

_LEGAL = re.compile(
    r"\b(public joint stock company|joint stock company|limited liability company|private limited company|"
    r"pjsc|ojsc|cjsc|jsc|pao|oao|zao|ooo|llc|l l c|ltd|limited|inc|incorporated|corp|corporation|co|company|"
    r"plc|p l c|gmbh|ag|sa|s a|spa|s p a|nv|n v|bv|b v|fze|fzco|llp|lp|the|of|and|"
    # legal-form words that survive word re-ordering, and Latin-American forms (S.A. de C.V., S. de R.L.)
    r"joint|stock|liability|de|cv|c v|rl|r l)\b"
)


def normalize_name(name: str, strip_legal: bool = True) -> str:
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    n = re.sub(r"[^a-z0-9 ]+", " ", n.replace("&", " and "))
    if strip_legal:
        n = _LEGAL.sub(" ", n)
    return " ".join(n.split())


@dataclass
class ScreeningHit:
    query: str
    query_role: str
    entry_id: str
    source_id: str
    category: str
    matched_name: str
    primary_name: str
    name_type: str
    score: float
    method: str
    entity_type: str
    programs: list[str]
    countries: list[str]
    listed_on: str | None
    document_id: str
    list_url: str
    list_retrieved_at: str
    remarks: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Name:
    entry_id: str
    text: str
    norm: str
    tokens: frozenset[str]
    name_type: str


@dataclass
class _Entry:
    id: str
    source_id: str
    category: str
    entity_type: str
    primary_name: str
    programs: list[str]
    countries: list[str]
    identifiers: list[dict[str, str]]
    listed_on: str | None
    remarks: str
    document_id: str
    names: list[_Name] = field(default_factory=list)


class ScreeningIndex:
    """In-memory index over the current watch-list versions; reloads automatically when lists change."""

    def __init__(self, db: Database, max_df: int = 1500) -> None:
        self.db = db
        self.max_df = max_df
        self._lock = threading.RLock()
        self._version: tuple[Any, ...] | None = None
        self._entries: dict[str, _Entry] = {}
        self._token_index: dict[str, list[_Name]] = {}
        self._idf: dict[str, float] = {}
        self._max_idf = 1.0
        self._ident_index: dict[str, list[str]] = {}
        self._docs: dict[str, tuple[str, datetime]] = {}

    def version_stamp(self) -> tuple[Any, ...]:
        with self.db.session() as s:
            return tuple(
                s.execute(
                    select(IngestionRun.source_id, func.max(IngestionRun.finished_at))
                    .where(IngestionRun.kind == "reference", IngestionRun.status == "succeeded")
                    .group_by(IngestionRun.source_id)
                    .order_by(IngestionRun.source_id)
                ).all()
            )

    def ensure_loaded(self) -> None:
        stamp = self.version_stamp()
        with self._lock:
            if stamp != self._version:
                self._load()
                self._version = stamp

    def _load(self) -> None:
        entries: dict[str, _Entry] = {}
        with self.db.session() as s:
            for e in s.scalars(select(WatchlistEntry).where(WatchlistEntry.is_current.is_(True))):
                entries[e.id] = _Entry(
                    e.id,
                    e.source_id,
                    e.category,
                    e.entity_type,
                    e.primary_name,
                    e.programs,
                    e.countries,
                    e.identifiers,
                    e.listed_on,
                    e.remarks[:500],
                    e.document_id,
                )
            rows = s.execute(
                select(WatchlistName.entry_id, WatchlistName.name, WatchlistName.normalized, WatchlistName.name_type)
                .join(WatchlistEntry, WatchlistEntry.id == WatchlistName.entry_id)
                .where(WatchlistEntry.is_current.is_(True))
            ).all()
            doc_ids = {e.document_id for e in entries.values()}
            docs = (
                {
                    d.id: (d.url, d.retrieved_at)
                    for d in s.scalars(select(SourceDocument).where(SourceDocument.id.in_(doc_ids)))
                }
                if doc_ids
                else {}
            )
        postings: dict[str, list[_Name]] = defaultdict(list)
        for entry_id, text, _stored_norm, ntype in rows:
            # re-normalise at load so a change to normalize_name() applies to lists ingested by earlier versions
            norm = normalize_name(text)
            if entry_id not in entries or not norm:
                continue
            nm = _Name(entry_id, text, norm, frozenset(norm.split()), ntype)
            entries[entry_id].names.append(nm)
            for t in nm.tokens:
                postings[t].append(nm)
        ident: dict[str, list[str]] = defaultdict(list)
        for ent in entries.values():
            for i in ent.identifiers:
                v = re.sub(r"[^A-Z0-9]", "", str(i.get("value", "")).upper())
                if len(v) >= 6:
                    ident[v].append(ent.id)
        n_names = max(1, sum(len(e.names) for e in entries.values()))
        idf = {t: math.log((1 + n_names) / (1 + len(p))) + 1 for t, p in postings.items()}
        self._entries, self._token_index, self._ident_index, self._docs = entries, dict(postings), dict(ident), docs
        self._idf, self._max_idf = idf, math.log(1 + n_names) + 1

    @property
    def size(self) -> int:
        return len(self._entries)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for e in self._entries.values():
            out[e.source_id] += 1
        return dict(out)

    # ------------------------------------------------------------------ matching
    def _w(self, t: str) -> float:
        """Inverse document frequency of a token across all list names (unseen tokens are maximally distinctive)."""
        return self._idf.get(t, self._max_idf)

    def _distinctive_covered(self, a: frozenset[str], b: frozenset[str]) -> bool:
        """The most distinctive token of ``a`` has a close counterpart (typo-tolerant) in ``b``."""
        top = max(a, key=self._w)
        return any(fuzz.ratio(top, t) >= 80 for t in b)

    def _score(self, q_norm: str, q_tokens: frozenset[str], cand: _Name, person: bool = False) -> float:
        base = fuzz.token_sort_ratio(q_norm, cand.norm) / 100
        subset = len(q_tokens) >= 2 and len(cand.tokens) >= 2 and (q_tokens <= cand.tokens or cand.tokens <= q_tokens)
        if subset:
            smaller, larger = sorted((q_tokens, cand.tokens), key=len)
            if person:
                # personal names: a missing middle name is normal in lists ("David L. Joyce" ~ "David Joyce") and must
                # surface as a potential match for the reviewer to confirm or discount on secondary identifiers
                ratio = len(smaller) / len(larger)
                return max(base, 0.9 if ratio >= 0.5 else 0.82)
            # organisations: subset bonus only when the shared tokens carry most of the information (IDF mass):
            # "Post Bank" inside "Japan Post Bank" is generic overlap, not a name match
            mass = sum(self._w(t) for t in smaller) / sum(self._w(t) for t in larger)
            base = max(base, 0.9 if mass >= 0.6 else 0.82)
        if base < 1.0 and not (
            self._distinctive_covered(q_tokens, cand.tokens) and self._distinctive_covered(cand.tokens, q_tokens)
        ):
            base *= 0.85  # similar-looking strings whose distinctive words differ ("Mercer" vs "Macera")
        return base

    def screen(
        self,
        name: str,
        role: str = "subject",
        entity_type: str | None = None,
        threshold: float = 0.88,
        identifiers: list[str] | None = None,
        categories: tuple[str, ...] = ("sanctions", "pep"),
    ) -> list[ScreeningHit]:
        self.ensure_loaded()
        q_norm = normalize_name(name)
        q_tokens = frozenset(q_norm.split())
        results: dict[str, ScreeningHit] = {}
        for raw_id in identifiers or []:
            v = re.sub(r"[^A-Z0-9]", "", raw_id.upper())
            for eid in self._ident_index.get(v, []):
                e = self._entries[eid]
                if e.category in categories:
                    results[eid] = self._hit(name, role, e, e.primary_name, "identifier", 1.0, f"identifier:{raw_id}")
        if not q_tokens:
            return list(results.values())
        cands: dict[int, _Name] = {}
        for t in q_tokens:
            post = self._token_index.get(t, [])
            if len(post) <= self.max_df or len(q_tokens) == 1:
                for nm in post:
                    cands[id(nm)] = nm
        for nm in cands.values():
            e = self._entries[nm.entry_id]
            if e.category not in categories:
                continue
            person = entity_type == "individual" or (entity_type is None and e.entity_type == "individual")
            score = self._score(q_norm, q_tokens, nm, person)
            if nm.name_type == "weak":
                score *= 0.92
            if entity_type and e.entity_type in ("individual", "entity") and e.entity_type != entity_type:
                score *= 0.85
            if score >= threshold and (nm.entry_id not in results or score > results[nm.entry_id].score):
                results[nm.entry_id] = self._hit(name, role, e, nm.text, nm.name_type, score, "name")
        return sorted(results.values(), key=lambda h: -h.score)

    def _hit(self, q: str, role: str, e: _Entry, matched: str, ntype: str, score: float, method: str) -> ScreeningHit:
        url, ts = self._docs.get(e.document_id, ("", None))
        return ScreeningHit(
            q,
            role,
            e.id,
            e.source_id,
            e.category,
            matched,
            e.primary_name,
            ntype,
            round(score, 3),
            method,
            e.entity_type,
            e.programs,
            e.countries,
            e.listed_on,
            e.document_id,
            url,
            iso(ts) or "",
            e.remarks,
        )
