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
from ...index.web_results_index import COLLECTION_NAME
from ...models import WebSearchResult
from ...settings import settings
from .base import ProviderRequestError, provider_retry_max_retries, run_clientless_provider
from ..options import SearchOptions

LOGGER = logging.getLogger(__name__)


class QdrantSearchError(ProviderRequestError):
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
        dense_embedding = list(query_embedding) if query_embedding is not None else None
        if dense_embedding is None:
            dense_embedding = await _embed_qdrant_query(
                query,
                deadline=max(5.0, settings.search_retrieve_budget_seconds * 0.8),
            )
        if not dense_embedding:
            return []

        sparse = encode_bm25(query)
        if not sparse or not sparse.get("indices"):
            sparse = {"indices": [], "values": []}

        client = AsyncQdrantClient(
            url=url,
            auth_token_provider=_qdrant_auth_token_provider(),
            timeout=int(settings.search_retrieve_budget_seconds),
            prefer_grpc=False,
            port=443,
            https=True,
        )
        try:
            sparse_vector = models.SparseVector(
                indices=sparse["indices"],  # type: ignore[arg-type]
                values=sparse["values"],  # type: ignore[arg-type]
            )

            try:
                result = await client.query_points(
                    collection_name=COLLECTION_NAME,
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
            except Exception as exc:
                message = str(exc)
                if "404" in message or "doesn't exist" in message or "Not Found" in message:
                    LOGGER.warning(
                        "Qdrant collection '%s' missing at %s; skipping vector recall "
                        "until the indexer recreates it",
                        COLLECTION_NAME,
                        url,
                    )
                    return []
                raise

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
        # Bounded retry honors a server-issued Retry-After; raw SDK errors are
        # normalized into the provider error contract (http_status, retryable).
        max_retries=provider_retry_max_retries("qdrant"),
    )


__all__ = ["search_qdrant", "QdrantSearchError", "QdrantConfigError"]
