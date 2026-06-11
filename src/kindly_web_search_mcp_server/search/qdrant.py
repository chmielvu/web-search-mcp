"""Qdrant hybrid search provider — reads from the Qdrant index."""

from __future__ import annotations

import logging

import httpx
from qdrant_client import AsyncQdrantClient, models

from ..embeddings import embed_query
from ..index.bm25_encoder import encode_bm25
from ..models import WebSearchResult
from .options import SearchOptions
from ..settings import settings

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

    # 1. Dense embedding for query
    query_embedding: list[float] | None = None
    try:
        query_embedding = await embed_query(query, timeout=15.0)
    except Exception as e:
        LOGGER.warning(f"Qdrant query embedding failed: {e}")
        return []

    if not query_embedding:
        return []

    # 2. Sparse BM25 vector for query
    sparse = encode_bm25(query)
    if not sparse or not sparse.get("indices"):
        sparse = {"indices": [], "values": []}

    # 3. Connect to Qdrant (no API key - public HF Space)
    url = settings.qdrant_space_url.strip()
    if not url:
        LOGGER.debug("Qdrant search disabled: KINDLY_QDRANT_SPACE_URL not set")
        return []

    try:
        client = AsyncQdrantClient(url=url, timeout=30)
    except Exception as e:
        LOGGER.warning(f"Qdrant client creation failed: {e}")
        return []

    # 4. Hybrid search: dense + sparse with RRF fusion
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

        # Map hits to WebSearchResult
        results: list[WebSearchResult] = []
        for hit in result.points:
            payload = hit.payload or {}
            url = payload.get("url", "")
            if not url:
                continue

            # Tag with provider for feedback loop prevention
            providers = ["qdrant"]

            results.append(
                WebSearchResult(
                    title=payload.get("title", ""),
                    link=url,
                    snippet=payload.get("snippet", ""),
                    domain=payload.get("domain"),
                    providers=providers,
                    score=hit.score,
                    raw_score=hit.score,
                )
            )

        return results

    except Exception as e:
        LOGGER.warning(f"Qdrant search failed: {e}")
        return []
    finally:
        try:
            await client.close()
        except Exception:
            pass


__all__ = ["search_qdrant", "QdrantSearchError", "QdrantConfigError"]