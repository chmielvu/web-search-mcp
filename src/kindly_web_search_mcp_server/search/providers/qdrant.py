"""Qdrant hybrid search provider — reads from the Qdrant index."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence

import httpx
from qdrant_client import AsyncQdrantClient, models

from ...embeddings import embed_query
from ...index.bm25_encoder import encode_bm25
from ...models import WebSearchResult
from ...settings import settings
from .base import run_clientless_provider
from ..options import SearchOptions

LOGGER = logging.getLogger(__name__)


class QdrantSearchError(RuntimeError):
    pass


class QdrantConfigError(QdrantSearchError):
    pass


_EMBEDDING_CACHE_TTL_SECONDS = 600.0
_EMBEDDING_CACHE_MAX_SIZE = 256
_EMBEDDING_CACHE_LOCK = asyncio.Lock()
_EMBEDDING_CACHE: dict[str, tuple[float, list[float]]] = {}
_EMBEDDING_INFLIGHT: dict[str, asyncio.Task[list[float]]] = {}


def _qdrant_auth_token_provider() -> Callable[[], str] | None:
    token = settings.hf_token.strip()
    if not token:
        return None
    return lambda: token


def _embedding_cache_key(query: str) -> str:
    return " ".join(query.casefold().split())


async def _embed_qdrant_query(query: str, *, deadline: float = 15.0) -> list[float]:
    key = _embedding_cache_key(query)
    now = time.monotonic()
    async with _EMBEDDING_CACHE_LOCK:
        cached = _EMBEDDING_CACHE.get(key)
        if cached and now - cached[0] <= _EMBEDDING_CACHE_TTL_SECONDS:
            return cached[1]
        task = _EMBEDDING_INFLIGHT.get(key)
        if task is None:
            task = asyncio.create_task(
                _compute_and_cache_embedding(key, query),
                name="qdrant-query-embedding",
            )
            _EMBEDDING_INFLIGHT[key] = task

    # Wait for the in-flight embedding, but with a deadline so a stuck
    # embedding service doesn't block the search pipeline indefinitely.
    try:
        return await asyncio.wait_for(task, timeout=deadline)
    except asyncio.TimeoutError:
        LOGGER.warning(
            "Qdrant embedding for %r timed out after %.1fs",
            query[:80],
            deadline,
        )
        raise
    except asyncio.CancelledError:
        task.cancel()
        raise


async def _compute_and_cache_embedding(key: str, query: str) -> list[float]:
    try:
        embedding = await embed_query(query, timeout=20.0)
        async with _EMBEDDING_CACHE_LOCK:
            if len(_EMBEDDING_CACHE) >= _EMBEDDING_CACHE_MAX_SIZE:
                oldest_key = min(
                    _EMBEDDING_CACHE,
                    key=lambda item: _EMBEDDING_CACHE[item][0],
                )
                _EMBEDDING_CACHE.pop(oldest_key, None)
            _EMBEDDING_CACHE[key] = (time.monotonic(), embedding)
        return embedding
    finally:
        async with _EMBEDDING_CACHE_LOCK:
            task = asyncio.current_task()
            if task is not None and _EMBEDDING_INFLIGHT.get(key) is task:
                _EMBEDDING_INFLIGHT.pop(key, None)


async def search_qdrant(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
    search_options: SearchOptions | None = None,
    query_embedding: Sequence[float] | None = None,
) -> list[WebSearchResult]:
    """Query Qdrant index using hybrid search (dense + sparse with RRF fusion).

    Returns results tagged with provider="qdrant" for feedback loop prevention.
    """
    if not query.strip():
        return []

    if num_results < 1:
        return []

    # 1. Dense embedding + sparse BM25 query vectors
    # 2. Connect to Qdrant (no API key - public HF Space)
    url = settings.qdrant_space_url.strip()
    if not url:
        LOGGER.debug("Qdrant search disabled: QDRANT_SPACE_URL not set")
        return []

    async def _request() -> list[WebSearchResult]:
        dense_embedding = query_embedding
        if dense_embedding is None:
            dense_embedding = await _embed_qdrant_query(
                query,
                deadline=max(5.0, settings.provider_group_deadline_seconds * 0.8),
            )
        if not dense_embedding:
            return []

        sparse = encode_bm25(query)
        if not sparse or not sparse.get("indices"):
            sparse = {"indices": [], "values": []}

        client = AsyncQdrantClient(
            url=url,
            auth_token_provider=_qdrant_auth_token_provider(),
            timeout=30,
            prefer_grpc=False,
            port=443,
            https=True,
        )
        try:
            sparse_vector = models.SparseVector(
                indices=sparse["indices"],  # type: ignore[arg-type]
                values=sparse["values"],  # type: ignore[arg-type]
            )

            result = await client.query_points(
                collection_name="web_results",
                prefetch=[
                    models.Prefetch(
                        query=dense_embedding,
                        using="dense",
                        limit=50,
                    ),
                    models.Prefetch(
                        query=sparse_vector,
                        using="sparse",
                        limit=50,
                    ),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=num_results,
                with_payload=True,
            )

            results: list[WebSearchResult] = []
            for hit in result.points:
                payload = hit.payload or {}
                hit_url = payload.get("url", "")
                if not hit_url:
                    continue
                results.append(
                    WebSearchResult(
                        title=payload.get("title", ""),
                        link=hit_url,
                        snippet=payload.get("snippet", ""),
                        domain=payload.get("domain"),
                        score=hit.score,
                        raw_score=hit.score,
                    )
                )
            return results
        finally:
            await client.close()

    return await run_clientless_provider(
        "qdrant",
        query,
        num_results,
        request=_request,
        parse_response=lambda results: results,
    )


__all__ = ["search_qdrant", "QdrantSearchError", "QdrantConfigError"]
