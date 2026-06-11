"""Qdrant hybrid search provider — reads from the Qdrant index."""

from __future__ import annotations

import logging

import httpx
from qdrant_client import AsyncQdrantClient, models

from ..embeddings import embed_query
from ..index.bm25_encoder import encode_bm25
from ..models import WebSearchResult
from ..settings import settings
from .base_provider import run_clientless_provider
from .options import SearchOptions

LOGGER = logging.getLogger(__name__)


class QdrantSearchError(RuntimeError):
    pass


class QdrantConfigError(QdrantSearchError):
    pass


async def search_qdrant(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
    search_options: SearchOptions | None = None,
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
        query_embedding = await embed_query(query, timeout=15.0)
        if not query_embedding:
            return []

        sparse = encode_bm25(query)
        if not sparse or not sparse.get("indices"):
            sparse = {"indices": [], "values": []}

        client = AsyncQdrantClient(url=url, timeout=30)
        try:
            sparse_vector = models.SparseVector(
                indices=sparse["indices"],
                values=sparse["values"],
            )

            result = await client.query_points(
                collection_name="web_results",
                prefetch=[
                    models.Prefetch(
                        query=query_embedding,
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
