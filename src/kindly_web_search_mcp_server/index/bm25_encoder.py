"""Lightweight BM25 sparse vector encoder using term hashing."""

from __future__ import annotations

import logging
import math
import re
from collections import Counter

logger = logging.getLogger(__name__)

HASH_SPACE = 1 << 20


def _term_index(term: str) -> int:
    return abs(hash(term)) % HASH_SPACE


def _tokenize(text: str) -> list[str]:
    text = text.lower()
    tokens = re.findall(r"[a-z0-9]+", text)
    return [t for t in tokens if len(t) >= 2]


def encode_bm25(text: str) -> dict[str, list[int] | list[float]]:
    """Encode text as a Qdrant-compatible BM25 sparse vector.

    Uses sublinear TF normalization (log(1 + tf) / sqrt(dl)), no IDF
    (no corpus statistics needed). Suitable for per-document indexing
    without a pre-built vocabulary.

    Returns:
        Dict with ``indices`` (list[int]) and ``values`` (list[float])
        ready to pass to ``models.SparseVector(...)``.
    """
    tokens = _tokenize(text)
    if not tokens:
        return {"indices": [], "values": []}

    tf = Counter(tokens)
    doc_len = len(tokens)
    norm = math.sqrt(doc_len) if doc_len else 1.0

    indices: list[int] = []
    values: list[float] = []
    for term, count in tf.items():
        weight = math.log(1 + count) / norm
        indices.append(_term_index(term))
        values.append(round(weight, 6))

    return {"indices": indices, "values": values}
