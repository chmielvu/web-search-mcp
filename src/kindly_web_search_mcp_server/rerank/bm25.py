"""Small-corpus BM25 scoring with identifier-aware tokenization."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

import bm25s

_TOKEN_RE = re.compile(
    r"[\w]+(?:[.+#_-][\w.+#_-]*)*|\.[A-Za-z][\w.#+_-]*|[\u3400-\u4dbf\u4e00-\u9fff]+",
    re.UNICODE,
)
_CJK_RE = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff]+$")
_COMPONENT_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


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
    if not candidate_texts:
        return []
    if len(candidate_texts) == 1:
        return [0.0]
    corpus = [tokenize_candidate(text) for text in candidate_texts]
    query_tokens = stable_unique(tokenize_candidate(query))
    if not query_tokens:
        return [0.0] * len(candidate_texts)
    retriever = bm25s.BM25(k1=1.2, b=0.75, method="lucene")
    retriever.index(corpus, show_progress=False)
    scores = retriever.get_scores(query_tokens)
    return [float(value) for value in scores]
