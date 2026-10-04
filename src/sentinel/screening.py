"""Sanctions / PEP watch-list screening with fuzzy name matching.

Combines token-sort similarity (robust to word order: "Rask, Viktor") with alias matching and a
configurable threshold. Every hit returns its score so analysts can adjudicate false positives.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_P = re.compile(r"[^a-z0-9 ]")


def _norm(s: str) -> str:
    return " ".join(sorted(_P.sub(" ", s.lower()).split()))


def similarity(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


@dataclass
class WatchlistEntry:
    name: str
    list_name: str
    category: str  # sanctions | pep | enforcement
    aliases: list[str] = field(default_factory=list)
    country: str | None = None
    reference: str | None = None


@dataclass
class ScreeningHit:
    query: str
    matched: str
    list_name: str
    category: str
    score: float
    reference: str | None = None


class Screener:
    def __init__(self, entries: list[WatchlistEntry], threshold: float = 0.88) -> None:
        self.entries = entries
        self.threshold = threshold

    @classmethod
    def from_json(cls, path: str | Path, threshold: float = 0.88) -> Screener:
        raw: list[dict[str, Any]] = json.loads(Path(path).read_text())
        return cls([WatchlistEntry(**r) for r in raw], threshold)

    def screen(self, name: str) -> list[ScreeningHit]:
        hits = []
        for e in self.entries:
            best = max(similarity(name, cand) for cand in [e.name, *e.aliases])
            if best >= self.threshold:
                hits.append(ScreeningHit(name, e.name, e.list_name, e.category, round(best, 3), e.reference))
        return sorted(hits, key=lambda h: -h.score)

    def screen_many(self, names: list[str]) -> list[ScreeningHit]:
        out: list[ScreeningHit] = []
        for n in dict.fromkeys(names):
            out += self.screen(n)
        return out
