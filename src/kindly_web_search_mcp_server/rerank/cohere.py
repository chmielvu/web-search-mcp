"""Cohere reranker client."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from ..settings import settings

_COHERE_CLIENTS: dict[asyncio.AbstractEventLoop, httpx.AsyncClient] = {}


def _get_cohere_client(timeout: float = 30.0) -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    client = _COHERE_CLIENTS.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=timeout)
        _COHERE_CLIENTS[loop] = client
    return client


def _apply_instruction(query: str, instruction: str | None) -> str:
    cleaned_instruction = (instruction or "").strip()
    if not cleaned_instruction:
        return query
    return f"{cleaned_instruction}\n\n{query}"


def _parse_rerank_results(
    data: dict[str, Any], document_count: int
) -> list[tuple[int, float]]:
    results = data.get("results")
    if not isinstance(results, list):
        raise ValueError("Cohere rerank response missing results list")

    ranked: list[tuple[int, float]] = []
    for item in results:
        if not isinstance(item, dict):
            raise ValueError("Cohere rerank result item is not an object")
        index = item.get("index")
        score = item.get("relevance_score")
        if not isinstance(index, int) or not 0 <= index < document_count:
            raise ValueError(f"Cohere rerank returned invalid index: {index!r}")
        if not isinstance(score, (int, float)):
            raise ValueError(f"Cohere rerank returned invalid score: {score!r}")
        ranked.append((index, float(score)))

    if not ranked and document_count:
        raise ValueError("Cohere rerank returned no ranked documents")
    return ranked


async def cohere_rerank(
    query: str,
    documents: list[str],
    *,
    api_key: str | None = None,
    model: str | None = None,
    top_n: int | None = None,
    instruction: str | None = None,
    timeout: float = 30.0,
    http_client: httpx.AsyncClient | None = None,
    base_url: str | None = None,
) -> list[tuple[int, float]]:
    """Rerank documents using Cohere's v2/rerank API."""

    if not documents:
        return []

    resolved_api_key = (
        api_key or settings.cohere_api_key or os.environ.get("COHERE_API_KEY", "")
    )
    if not resolved_api_key.strip():
        raise ValueError("COHERE_API_KEY is required for Cohere reranking")

    payload = {
        "model": model or settings.cohere_rerank_model,
        "query": _apply_instruction(query, instruction),
        "documents": documents,
        "top_n": top_n or len(documents),
        "max_tokens_per_doc": 4096,
    }
    headers = {"Authorization": f"Bearer {resolved_api_key.strip()}"}
    endpoint = (base_url or settings.cohere_rerank_base_url).strip()
    if not endpoint:
        endpoint = "https://api.cohere.com/v2/rerank"

    client = http_client or _get_cohere_client(timeout)
    response = await client.post(endpoint, json=payload, headers=headers)
    response.raise_for_status()
    return _parse_rerank_results(response.json(), len(documents))
