"""Cohere reranker client."""

from __future__ import annotations

import asyncio
import math
import os
from typing import Any

import httpx

from ..settings import settings

_COHERE_CLIENTS: dict[asyncio.AbstractEventLoop, httpx.AsyncClient] = {}


def _get_cohere_client(timeout: float = 5.0) -> httpx.AsyncClient:
    """Return a loop-scoped client.

    Note: httpx bakes Timeout into the client at construction. Callers that
    need a different timeout MUST pass timeout= on the request (see
    cohere_rerank), not rely on this factory alone.
    """
    loop = asyncio.get_running_loop()
    client = _COHERE_CLIENTS.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=timeout)
        _COHERE_CLIENTS[loop] = client
    return client


def _parse_rerank_results(data: dict[str, Any], document_count: int) -> list[tuple[int, float]]:
    """Parse Cohere POST /v2/rerank response.

    Cohere contract (docs.cohere.com/v2/reference/rerank):
    - top_n limits returned results; if omitted, all results are returned
    - each result requires index + relevance_score
    - relevance_score is normalized to [0, 1] (clamp minor drift)

    Accept any non-empty results list with 0 < len <= document_count.
    Do not require a full permutation of input indices.
    """
    results = data.get("results")
    if not isinstance(results, list):
        raise ValueError("Cohere rerank response missing results list")
    if not results:
        raise ValueError("Cohere rerank response returned no results")
    if len(results) > document_count:
        raise ValueError(
            f"Cohere rerank returned {len(results)} results, expected at most {document_count}"
        )

    ranked: list[tuple[int, float]] = []
    seen_indices: set[int] = set()
    for item in results:
        if not isinstance(item, dict):
            raise ValueError("Cohere rerank result item is not an object")
        index = item.get("index")
        score = item.get("relevance_score")

        if not isinstance(index, int) or isinstance(index, bool):
            raise ValueError(f"Cohere rerank returned non-integer index: {index!r}")
        if not 0 <= index < document_count:
            raise ValueError(f"Cohere rerank returned index out of bounds: {index!r}")
        if index in seen_indices:
            raise ValueError(f"Cohere rerank returned duplicate index: {index!r}")
        seen_indices.add(index)

        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError(f"Cohere rerank returned non-numeric score: {score!r}")
        f_score = float(score)
        if not math.isfinite(f_score):
            raise ValueError(f"Cohere rerank returned non-finite score: {score!r}")
        if f_score < 0.0:
            f_score = 0.0
        elif f_score > 1.0:
            f_score = 1.0

        ranked.append((index, f_score))

    return ranked


async def cohere_rerank(
    query: str,
    documents: list[str],
    *,
    api_key: str | None = None,
    model: str | None = None,
    top_n: int | None = None,
    timeout: float = 5.0,
    http_client: httpx.AsyncClient | None = None,
    base_url: str | None = None,
) -> list[tuple[int, float]]:
    """Rerank documents using Cohere's v2/rerank API."""

    if not documents:
        return []

    resolved_api_key = api_key or settings.cohere_api_key or os.environ.get("COHERE_API_KEY", "")
    if not resolved_api_key.strip():
        raise ValueError("COHERE_API_KEY is required for Cohere reranking")

    payload = {
        "model": model or os.environ.get("COHERE_RERANK_MODEL", "rerank-v4.0-fast"),
        "query": query,
        "documents": documents,
        "top_n": top_n or len(documents),
        "max_tokens_per_doc": 4096,
    }
    headers = {"Authorization": f"Bearer {resolved_api_key.strip()}"}
    endpoint = (base_url or settings.cohere_rerank_base_url).strip()
    if not endpoint:
        endpoint = "https://api.cohere.com/v2/rerank"

    client = http_client or _get_cohere_client(timeout)
    # Per-request timeout so a cached client cannot silently keep a longer
    # default and block the fallback chain for 30s.
    response = await client.post(endpoint, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    return _parse_rerank_results(response.json(), len(documents))
