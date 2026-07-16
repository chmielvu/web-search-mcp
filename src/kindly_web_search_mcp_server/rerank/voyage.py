"""Voyage AI reranker client."""

from __future__ import annotations

import os
import math
from typing import Any

import httpx

from ..settings import settings

VOYAGE_RERANK_ENDPOINT = "https://api.voyageai.com/v1/rerank"

_VOYAGE_CLIENT: httpx.AsyncClient | None = None


def _get_voyage_client(timeout: float = 30.0) -> httpx.AsyncClient:
    global _VOYAGE_CLIENT
    if _VOYAGE_CLIENT is None or _VOYAGE_CLIENT.is_closed:
        _VOYAGE_CLIENT = httpx.AsyncClient(timeout=timeout)
    return _VOYAGE_CLIENT


def _parse_rerank_results(data: dict[str, Any], document_count: int) -> list[tuple[int, float]]:
    results = data.get("data")
    if not isinstance(results, list):
        raise ValueError("Voyage rerank response missing data list")

    if len(results) != document_count:
        raise ValueError(
            f"Voyage rerank returned {len(results)} results, expected {document_count}"
        )
    ranked: list[tuple[int, float]] = []
    seen_indices: set[int] = set()
    for item in results:
        if not isinstance(item, dict):
            raise ValueError("Voyage rerank result item is not an object")
        index = item.get("index")
        score = item.get("relevance_score")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < document_count:
            raise ValueError(f"Voyage rerank returned invalid index: {index!r}")
        if index in seen_indices:
            raise ValueError(f"Voyage rerank returned duplicate index: {index!r}")
        if isinstance(score, bool) or not isinstance(score, int | float):
            raise ValueError(f"Voyage rerank returned invalid score: {score!r}")
        numeric_score = float(score)
        if not math.isfinite(numeric_score) or not 0.0 <= numeric_score <= 1.0:
            raise ValueError(f"Voyage rerank returned invalid score: {score!r}")
        seen_indices.add(index)
        ranked.append((index, numeric_score))
    return ranked


async def voyage_rerank(
    query: str,
    documents: list[str],
    *,
    api_key: str | None = None,
    model: str | None = None,
    top_n: int | None = None,
    timeout: float = 30.0,
    http_client: httpx.AsyncClient | None = None,
) -> list[tuple[int, float]]:
    """Rerank documents using Voyage's /v1/rerank API."""

    if not documents:
        return []

    resolved_api_key = api_key or settings.voyage_api_key or os.environ.get("VOYAGE_API_KEY", "")
    if not resolved_api_key.strip():
        raise ValueError("VOYAGE_API_KEY is required for Voyage reranking")

    payload = {
        "model": model or settings.voyage_rerank_model,
        "query": query,
        "documents": documents,
        "top_k": top_n or len(documents),
        "return_documents": False,
        "truncation": True,
    }
    headers = {"Authorization": f"Bearer {resolved_api_key}"}

    client = http_client or _get_voyage_client(timeout)
    response = await client.post(VOYAGE_RERANK_ENDPOINT, json=payload, headers=headers)
    response.raise_for_status()
    return _parse_rerank_results(response.json(), len(documents))
