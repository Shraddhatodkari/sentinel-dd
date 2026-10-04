"""Core domain models shared across layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Document:
    id: str
    title: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Chunk:
    id: str  # "<doc_id>#<n>" — also the citation key
    doc_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScoredChunk:
    chunk: Chunk
    score: float
    source: str = "hybrid"  # dense | sparse | hybrid | graph

    @property
    def citation(self) -> str:
        return f"[{self.chunk.id}]"


@dataclass
class Evidence:
    question: str
    answer: str
    citations: list[str]
    grounding_score: float
    chunks: list[ScoredChunk] = field(default_factory=list)
    flagged_injection: bool = False
