"""Qdrant hybrid search provider — reads from the Qdrant index."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable, Sequence

import httpx
from qdrant_client import AsyncQdrantClient, models

from ...index.bm25_encoder import encode_bm25
from ...index.web_results_index import COLLECTION_NAME
from ...ml import embed_query
from ...settings import settings
from ...utils.url_canonicalize import extract_domain_from_url
from ..options import SearchOptions
from ..types import AnswerKind, EngineCall, SearchHit, SourceKind
from .base import ProviderRequestError, provider_retry_max_retries, run_clientless_provider

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


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


_SOURCE_KINDS: dict[str, SourceKind] = {
    "forum": "forum",
    "video": "video",
    "social": "social",
    "official": "official",
    "news": "news",
    "docs": "docs",
    "other": "other",
}
_ANSWER_KINDS: dict[str, AnswerKind] = {
    "featured_snippet": "featured_snippet",
    "paa": "paa",
    "knowledge": "knowledge",
    "llm": "llm",
    "forum": "forum",
}


def _source_kind(value: object) -> SourceKind | None:
    return _SOURCE_KINDS.get(value) if isinstance(value, str) else None


def _answer_kind(value: object) -> AnswerKind | None:
    return _ANSWER_KINDS.get(value) if isinstance(value, str) else None


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
    except TimeoutError:
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
) -> EngineCall:
    """Query Qdrant index using hybrid search (dense + sparse with RRF fusion).

    Returns a typed engine call whose hits carry the indexed page testimony.
    """
    if not query.strip():
        return EngineCall(adapter="qdrant", query=query)

    if num_results < 1:
        return EngineCall(adapter="qdrant", query=query)

    # 1. Dense embedding + sparse BM25 query vectors
    # 2. Connect to Qdrant (no API key - public HF Space)
    url = settings.qdrant_space_url.strip()
    if not url:
        LOGGER.debug("Qdrant search disabled: QDRANT_SPACE_URL not set")
        return EngineCall(adapter="qdrant", query=query)

    async def _request() -> list[SearchHit]:
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

            hits: list[SearchHit] = []
            for hit in result.points:
                payload = hit.payload or {}
                hit_url = payload.get("url", "")
                if not hit_url:
                    continue
                domain = payload.get("domain") or extract_domain_from_url(str(hit_url))
                if not domain:
                    continue
                engine_rank_value = payload.get("engine_rank")
                engine_rank = (
                    None
                    if isinstance(engine_rank_value, bool) or not isinstance(engine_rank_value, int)
                    else engine_rank_value
                )
                raw_score = getattr(hit, "score", None)
                provider_score = (
                    float(raw_score)
                    if not isinstance(raw_score, bool)
                    and isinstance(raw_score, (int, float))
                    and math.isfinite(raw_score)
                    else None
                )
                source_name_value = payload.get("source_name")
                published_value = payload.get("published")
                hits.append(
                    SearchHit(
                        title=str(payload.get("title", "")),
                        url=str(hit_url),
                        snippet=str(payload.get("snippet", "")),
                        domain=str(domain),
                        adapter="qdrant",
                        engine_rank=engine_rank,
                        provider_score=provider_score,
                        source_name=(
                            source_name_value.strip()
                            if isinstance(source_name_value, str) and source_name_value.strip()
                            else None
                        ),
                        source_kind=_source_kind(payload.get("source_kind")),
                        published=(
                            published_value.strip()
                            if isinstance(published_value, str) and published_value.strip()
                            else None
                        ),
                        highlights=_string_tuple(payload.get("highlights")),
                        source_engines=_string_tuple(payload.get("source_engines")),
                        origin_adapters=_string_tuple(
                            payload.get("origin_adapters") or payload.get("provider")
                        ),
                        answer_kind=_answer_kind(payload.get("answer_kind")),
                    )
                )
            return hits
        finally:
            await client.close()

    return await run_clientless_provider(
        "qdrant",
        query,
        num_results,
        request=_request,
        parse_response=lambda hits: EngineCall(adapter="qdrant", query=query, hits=tuple(hits)),
        # Bounded retry honors a server-issued Retry-After; raw SDK errors are
        # normalized into the provider error contract (http_status, retryable).
        max_retries=provider_retry_max_retries("qdrant"),
    )


__all__ = ["QdrantConfigError", "QdrantSearchError", "search_qdrant"]
