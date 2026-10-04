"""Structured data source (company registry / CRM / data warehouse) with fuzzy entity lookup.

Structured retrieval pattern: numerical facts (revenue, leverage, filings) come from a typed
source of truth rather than being parsed out of free text by an LLM — eliminating a whole class
of hallucination for the ML feature pipeline.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

from sentinel.graphrag.graph import normalize


class EntityRegistry:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._by_key = {normalize(r["name"]): r for r in records}

    @classmethod
    def from_json(cls, path: str | Path) -> EntityRegistry:
        return cls(json.loads(Path(path).read_text()))

    def lookup(self, name: str, cutoff: float = 0.85) -> dict[str, Any] | None:
        key = normalize(name)
        if key in self._by_key:
            return dict(self._by_key[key])
        m = difflib.get_close_matches(key, list(self._by_key), n=1, cutoff=cutoff)
        return dict(self._by_key[m[0]]) if m else None

    def names(self) -> list[str]:
        return [r["name"] for r in self._by_key.values()]

    def upsert(self, record: dict[str, Any]) -> None:
        self._by_key[normalize(record["name"])] = record
