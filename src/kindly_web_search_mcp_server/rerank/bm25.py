"""Small-corpus BM25 scoring with identifier-aware tokenization."""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections.abc import Sequence

import bm25s

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(
    r"[\w]+(?:[.+#_-][\w.+#_-]*)*|\.[A-Za-z][\w.#+_-]*|[\u3400-\u4dbf\u4e00-\u9fff]+",
    re.UNICODE,
)
_CJK_RE = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff]+$")
_COMPONENT_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)

# Serialise BM25 native code to prevent concurrent scipy/numpy calls
# from corrupting BLAS state when multiple asyncio tasks call
# score_candidates simultaneously.
_BM25_LOCK = asyncio.Lock()


def stable_unique(tokens: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(tokens))


def tokenize_candidate(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(normalized):
        token = match.group(0)
        if _CJK_RE.fullmatch(token):
            tokens.extend(token)
            tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
            continue
        tokens.append(token)
        if any(marker in token for marker in (".", "+", "#", "_", "-")):
            tokens.extend(component for component in _COMPONENT_RE.findall(token) if component)
    return tokens


def score_candidates(query: str, candidate_texts: Sequence[str]) -> list[float]:
    """Synchronous BM25 scoring — safe for single-threaded or locked contexts.

    With a single candidate, every term's IDF is log(1/1) = 0 so BM25
    scores are trivially zero.  The caller already handles this edge case.
    """
    if not candidate_texts:
        return []
    if len(candidate_texts) == 1:
        return [0.0]
    corpus = [tokenize_candidate(text) for text in candidate_texts]
    query_tokens = stable_unique(tokenize_candidate(query))
    if not query_tokens:
        logger.debug("BM25: query tokenized to empty token list; returning all zeros")
        return [0.0] * len(candidate_texts)
    retriever = bm25s.BM25(k1=1.2, b=0.75, method="lucene")
    retriever.index(corpus, show_progress=False)
    scores = retriever.get_scores(query_tokens)
    return [float(value) for value in scores]


async def score_candidates_async(query: str, candidate_texts: Sequence[str]) -> list[float]:
    """Async-guarded BM25 scoring — serialises native code across asyncio tasks."""
    async with _BM25_LOCK:
        return score_candidates(query, candidate_texts)
