"""Embedding backends.

* :class:`HashingEmbedder` — deterministic, dependency-free signed feature hashing over word
  unigrams/bigrams + character trigrams. Good lexical-semantic recall, zero network, ideal for
  CI and air-gapped deployments.
* :class:`SentenceTransformerEmbedder` — transformer embeddings (e.g. ``BAAI/bge-small-en-v1.5``).
* :class:`OpenAIEmbedder` — hosted embeddings (``text-embedding-3-small``).
"""

from __future__ import annotations

import hashlib
import itertools
import re
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Any

import numpy as np

_WORD = re.compile(r"[a-z0-9]+")


class Embedder(ABC):
    dim: int

    @abstractmethod
    def embed(self, texts: list[str]) -> np.ndarray:
        """Return an (n, dim) float32 matrix of L2-normalised vectors."""

    def embed_one(self, text: str) -> np.ndarray:
        return self.embed([text])[0]


def _l2(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (m / norms).astype(np.float32)


@lru_cache(maxsize=200_000)
def _bucket(feature: str, dim: int) -> tuple[int, float]:
    h = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "little")
    return h % dim, (1.0 if (h >> 63) & 1 else -1.0)


class HashingEmbedder(Embedder):
    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def _features(self, text: str) -> list[tuple[str, float]]:
        words = _WORD.findall(text.lower())
        feats: list[tuple[str, float]] = [(f"w:{w}", 1.0) for w in words]
        feats += [(f"b:{a}_{b}", 0.7) for a, b in itertools.pairwise(words)]
        for w in words:
            padded = f"#{w}#"
            feats += [(f"c:{padded[i : i + 3]}", 0.25) for i in range(len(padded) - 2)]
        return feats

    def embed(self, texts: list[str]) -> np.ndarray:
        m = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for feat, w in self._features(t):
                idx, sign = _bucket(feat, self.dim)
                m[i, idx] += sign * w
        # sublinear tf damping
        m = np.sign(m) * np.log1p(np.abs(m))
        return _l2(m)


class SentenceTransformerEmbedder(Embedder):  # pragma: no cover - requires model download
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self._model.encode(texts, normalize_embeddings=True), dtype=np.float32)


class OpenAIEmbedder(Embedder):
    def __init__(
        self, api_key: str = "", model: str = "text-embedding-3-small", dim: int = 1536, client: Any | None = None
    ) -> None:
        if client is None:  # pragma: no cover
            from openai import OpenAI

            client = OpenAI(api_key=api_key)
        self._client = client
        self.model = model
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), 256):  # batch to respect API limits
            resp = self._client.embeddings.create(model=self.model, input=texts[i : i + 256])
            out += [d.embedding for d in resp.data]
        return _l2(np.asarray(out, dtype=np.float32))


def build_embedder(backend: str, dim: int = 384, **kwargs: Any) -> Embedder:
    if backend == "hashing":
        return HashingEmbedder(dim)
    if backend == "sentence_transformers":  # pragma: no cover
        return SentenceTransformerEmbedder(**kwargs)
    if backend == "openai":  # pragma: no cover
        return OpenAIEmbedder(**kwargs)
    raise ValueError(f"unknown embedding backend {backend}")
