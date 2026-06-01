"""Jina AI reranker client."""

from __future__ import annotations

import os
from typing import Any

import httpx

from ..settings import settings

JINA_RERANK_ENDPOINT = "https://api.jina.ai/v1/rerank"

_JINA_CLIENT: httpx.AsyncClient | None = None


def _get_jina_client(timeout: float = 30.0) -> httpx.AsyncClient:
    global _JINA_CLIENT
    if _JINA_CLIENT is None or _JINA_CLIENT.is_closed:
        _JINA_CLIENT = httpx.AsyncClient(timeout=timeout)
    return _JINA_CLIENT


def _parse_rerank_results(
    data: dict[str, Any], document_count: int
) -> list[tuple[int, float]]:
    results = data.get("results")
    if not isinstance(results, list):
        raise ValueError("Jina rerank response missing results list")

    ranked: list[tuple[int, float]] = []
    for item in results:
        if not isinstance(item, dict):
            raise ValueError("Jina rerank result item is not an object")
        index = item.get("index")
        score = item.get("relevance_score")
        if not isinstance(index, int) or not 0 <= index < document_count:
            raise ValueError(f"Jina rerank returned invalid index: {index!r}")
        if not isinstance(score, int | float):
            raise ValueError(f"Jina rerank returned invalid score: {score!r}")
        ranked.append((index, float(score)))

    if not ranked and document_count:
        raise ValueError("Jina rerank returned no ranked documents")
    return ranked


async def jina_rerank(
    query: str,
    documents: list[str | dict],
    *,
    api_key: str | None = None,
    model: str | None = None,
    top_n: int | None = None,
    timeout: float = 30.0,
    http_client: httpx.AsyncClient | None = None,
) -> list[tuple[int, float]]:
    """Rerank documents using Jina's /v1/rerank API.

    Documents can be plain strings or structured dicts with ``{"text": ..., "title": ...}``
    keys, which gives the model proper title/body separation (supported by
    jina-reranker-v2-base-multilingual and later).
    """
    if not documents:
        return []
    resolved_api_key = api_key or os.environ.get("JINA_API_KEY", "")
    if not resolved_api_key.strip():
        raise ValueError("JINA_API_KEY is required for Jina reranking")

    payload = {
        "model": model or settings.jina_rerank_model,
        "query": query,
        "documents": documents,
        "top_n": top_n or len(documents),
        "return_documents": False,
    }
    headers = {"Authorization": f"Bearer {resolved_api_key}"}

    if http_client is not None:
        response = await http_client.post(
            JINA_RERANK_ENDPOINT, json=payload, headers=headers
        )
        response.raise_for_status()
        return _parse_rerank_results(response.json(), len(documents))

    client = _get_jina_client(timeout)
    response = await client.post(JINA_RERANK_ENDPOINT, json=payload, headers=headers)
    response.raise_for_status()
    return _parse_rerank_results(response.json(), len(documents))
