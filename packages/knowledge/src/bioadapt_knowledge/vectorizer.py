"""Embedder abstractions and deterministic offline feature-hashing embedder.

Provides:
- `Embedder` protocol.
- `DeterministicTfidfEmbedder`: 100% offline, pure-Python feature-hashing vectorizer
  with sublinear term weighting and L2 normalization. Works on mixed English/Chinese
  scientific texts, gene names, and cell types without external network or ML models.
- `cosine_similarity`: fast inner product of L2-normalized vectors.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, Sequence, runtime_checkable

WORD_OR_CHAR_RE = re.compile(r"[A-Za-z0-9_\-\+]+|[\u4e00-\u9fff]")


def cosine_similarity(v1: Sequence[float], v2: Sequence[float]) -> float:
    """Compute cosine similarity between two vectors.

    If vectors are already L2-normalized, this is equivalent to dot product.
    """
    if len(v1) != len(v2) or not v1:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for a, b in zip(v1, v2):
        dot += a * b
        norm_a += a * a
        norm_b += b * b
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(dot / (math.sqrt(norm_a) * math.sqrt(norm_b)))


@runtime_checkable
class Embedder(Protocol):
    """Protocol for vector embedders."""

    @property
    def dimension(self) -> int:
        ...

    def embed_query(self, text: str) -> list[float]:
        ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        ...


class DeterministicTfidfEmbedder:
    """Pure-Python offline embedder based on Feature Hashing (Hashing Trick) and n-grams.

    Guarantees:
    - 0 network calls (STRICT_LOCAL compliance).
    - 0 torch/transformers dependency.
    - Deterministic output across Python runtimes and architectures.
    - Handles mixed scientific nomenclature: CD4+ T, Foxp3, 巨噬细胞, etc.
    """

    def __init__(self, dimension: int = 256, ngram_range: tuple[int, int] = (1, 2)) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self._dim = dimension
        self._ngram_min, self._ngram_max = ngram_range

    @property
    def dimension(self) -> int:
        return self._dim

    def _tokenize(self, text: str) -> list[str]:
        tokens: list[str] = []
        for match in WORD_OR_CHAR_RE.finditer(text.lower()):
            tokens.append(match.group(0))
        return tokens

    def _extract_ngrams(self, tokens: list[str]) -> list[str]:
        ngrams: list[str] = []
        n_tokens = len(tokens)
        for n in range(self._ngram_min, self._ngram_max + 1):
            for i in range(n_tokens - n + 1):
                ngrams.append(" ".join(tokens[i : i + n]))
        return ngrams

    def _vectorize_single(self, text: str) -> list[float]:
        tokens = self._tokenize(text)
        if not tokens:
            return [0.0] * self._dim

        ngrams = self._extract_ngrams(tokens)
        counts: dict[int, float] = {}

        for ng in ngrams:
            # Deterministic MD5 hash bucket and sign
            h = hashlib.md5(ng.encode("utf-8")).digest()
            bucket = int.from_bytes(h[:4], "little") % self._dim
            sign = 1.0 if (h[4] & 1) else -1.0
            counts[bucket] = counts.get(bucket, 0.0) + sign

        # Sublinear scaling: 1 + ln(tf)
        vec = [0.0] * self._dim
        norm_sq = 0.0
        for bucket, val in counts.items():
            if val != 0.0:
                scaled = math.copysign(1.0 + math.log(abs(val)), val)
                vec[bucket] = scaled
                norm_sq += scaled * scaled

        # L2 normalize
        if norm_sq > 0.0:
            norm = math.sqrt(norm_sq)
            vec = [v / norm for v in vec]

        return vec

    def embed_query(self, text: str) -> list[float]:
        return self._vectorize_single(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vectorize_single(t) for t in texts]
