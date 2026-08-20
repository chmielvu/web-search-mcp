"""OpenRouter Cohere reranker client."""

from __future__ import annotations

import asyncio
import math
import os
from typing import Any
import weakref

import httpx

from ..settings import settings

OPENROUTER_RERANK_ENDPOINT = "https://openrouter.ai/api/v1/rerank"

_OPENROUTER_CLIENTS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient] = (
    weakref.WeakKeyDictionary()
)


def _get_openrouter_client(timeout: float = 30.0) -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    client = _OPENROUTER_CLIENTS.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=timeout)
        _OPENROUTER_CLIENTS[loop] = client
    return client


def _parse_rerank_results(data: dict[str, Any], document_count: int) -> list[tuple[int, float]]:
    """Parse OpenRouter POST /api/v1/rerank response.

    OpenRouter contract (submit-a-rerank-request):
    - required top-level: model, results
    - each result required: index, relevance_score, document
    - top_n = "Number of most relevant documents to return" (a CAP, not a
      guarantee of len(results) == len(documents))

    Accept any non-empty results list with 0 < len <= document_count.
    Do not require a full permutation of input indices.
    """
    results = data.get("results")
    if not isinstance(results, list):
        raise ValueError("OpenRouter rerank response missing results list")
    if not results:
        raise ValueError("OpenRouter rerank response returned no results")
    if len(results) > document_count:
        raise ValueError(
            f"OpenRouter rerank returned {len(results)} results, expected at most {document_count}"
        )

    ranked: list[tuple[int, float]] = []
    seen_indices: set[int] = set()
    for item in results:
        if not isinstance(item, dict):
            raise ValueError("OpenRouter rerank result item is not an object")
        index = item.get("index")
        score = item.get("relevance_score")

        if not isinstance(index, int) or isinstance(index, bool):
            raise ValueError(f"OpenRouter rerank returned non-integer index: {index!r}")
        if not 0 <= index < document_count:
            raise ValueError(f"OpenRouter rerank returned index out of bounds: {index!r}")
        if index in seen_indices:
            raise ValueError(f"OpenRouter rerank returned duplicate index: {index!r}")
        seen_indices.add(index)

        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError(f"OpenRouter rerank returned non-numeric score: {score!r}")
        f_score = float(score)
        if not math.isfinite(f_score):
            raise ValueError(f"OpenRouter rerank returned non-finite score: {score!r}")
        # Spec is double relevance; clamp out-of-range rather than fail the chain.
        if f_score < 0.0:
            f_score = 0.0
        elif f_score > 1.0:
            f_score = 1.0

        ranked.append((index, f_score))

    return ranked


async def openrouter_cohere_rerank(
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
    """Rerank documents with Cohere's rerank-4-fast through OpenRouter."""

    if not documents:
        return []

    resolved_api_key = (
        api_key or settings.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
    )
    if not resolved_api_key.strip():
        raise ValueError("OPENROUTER_API_KEY is required for OpenRouter reranking")

    payload = {
        "model": model or settings.openrouter_rerank_model,
        "query": query,
        "documents": documents,
        "top_n": top_n or len(documents),
    }
    headers = {
        "Authorization": f"Bearer {resolved_api_key.strip()}",
        "Content-Type": "application/json",
    }
    endpoint = (base_url or settings.openrouter_rerank_base_url).strip()
    if not endpoint:
        endpoint = OPENROUTER_RERANK_ENDPOINT

    client = http_client or _get_openrouter_client(timeout)
    response = await client.post(endpoint, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    return _parse_rerank_results(response.json(), len(documents))
