"""Agent memory.

* :class:`ShortTermMemory` — token-budgeted working memory. When the budget is exceeded the oldest
  turns are compressed into a rolling summary (LLM-based or extractive), keeping the most recent
  turns verbatim. Bounded context => bounded cost/latency.
* :class:`LongTermMemory` — semantic episodic memory of past cases/decisions. Lets agents recall
  prior findings on the same counter-party ("we reviewed Helios Trade last quarter...").
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from sentinel.embeddings.base import Embedder
from sentinel.llm.base import LLMClient, Message


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class ShortTermMemory:
    max_tokens: int = 2000
    keep_recent: int = 4
    llm: LLMClient | None = None
    summary: str = ""
    turns: list[Message] = field(default_factory=list)

    def add(self, role: str, content: str) -> None:
        self.turns.append(Message(role, content))  # type: ignore[arg-type]
        self._compact()

    def tokens(self) -> int:
        return approx_tokens(self.summary) + sum(approx_tokens(m.content) for m in self.turns)

    def _compact(self) -> None:
        while self.tokens() > self.max_tokens and len(self.turns) > self.keep_recent:
            old = self.turns[: len(self.turns) - self.keep_recent]
            self.turns = self.turns[len(old) :]
            transcript = "\n".join(f"{m.role}: {m.content}" for m in old)
            if self.llm is not None:
                resp = self.llm.complete(
                    [
                        Message(
                            "user", f"Summarise preserving facts, entities, citations:\n{self.summary}\n{transcript}"
                        )
                    ],
                    max_tokens=300,
                )
                self.summary = resp.text[: self.max_tokens * 2]
            else:  # extractive fallback: keep first sentence of each turn
                firsts = [m.content.split(". ")[0][:200] for m in old]
                self.summary = (self.summary + " | " + " | ".join(firsts)).strip(" |")[-self.max_tokens * 2 :]
        # if even the recent turns exceed the budget, truncate the summary
        if self.tokens() > self.max_tokens:
            self.summary = self.summary[-self.max_tokens :]

    def context(self) -> list[Message]:
        prefix = [Message("user", f"[Conversation summary] {self.summary}")] if self.summary else []
        return prefix + list(self.turns)


@dataclass
class MemoryRecord:
    key: str
    text: str
    metadata: dict[str, Any]
    ts: float


class LongTermMemory:
    def __init__(self, embedder: Embedder, max_items: int = 10_000) -> None:
        self.embedder = embedder
        self.max_items = max_items
        self._records: list[MemoryRecord] = []
        self._vecs = np.zeros((0, embedder.dim), dtype=np.float32)
        self._lock = threading.Lock()

    def remember(self, key: str, text: str, **metadata: Any) -> None:
        vec = self.embedder.embed_one(text)
        with self._lock:
            self._records.append(MemoryRecord(key, text, metadata, time.time()))
            self._vecs = np.vstack([self._vecs, vec[None, :]])
            if len(self._records) > self.max_items:  # FIFO eviction
                self._records = self._records[1:]
                self._vecs = self._vecs[1:]

    def recall(self, query: str, k: int = 3, min_score: float = 0.2, **flt: Any) -> list[tuple[MemoryRecord, float]]:
        with self._lock:
            if not self._records:
                return []
            scores = self._vecs @ self.embedder.embed_one(query)
            out = []
            for i in np.argsort(-scores):
                r = self._records[i]
                if scores[i] < min_score:
                    break
                if all(r.metadata.get(kk) == v for kk, v in flt.items()):
                    out.append((r, float(scores[i])))
                if len(out) >= k:
                    break
            return out

    def __len__(self) -> int:
        return len(self._records)
