"""Remote Qdrant index for web search results — write-only (Phase 0)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from hashlib import sha256
from typing import Awaitable
import uuid

from qdrant_client import AsyncQdrantClient, models

from ..models import WebSearchResult
from ..settings import settings

logger = logging.getLogger(__name__)

_web_results_index: WebResultsIndex | None = None

COLLECTION_NAME = "web_results"
COLLECTION_VECTORS = {
    "dense": models.VectorParams(
        size=384,
        distance=models.Distance.COSINE,
    ),
}
COLLECTION_SPARSE = {
    "sparse": models.SparseVectorParams(
        index=models.SparseIndexParams(on_disk=False),
    ),
}


def _uuid_from_url(url: str) -> str:
    digest = sha256(url.strip().encode("utf-8")).hexdigest()
    return str(uuid.UUID(digest[:32]))


class WebResultsIndex:
    """Asynchronous write-only index for final search results stored on HF Space Qdrant."""

    def __init__(
        self,
        *,
        url: str,
        api_key: str | None = None,
        auth_token_provider: Callable[[], str | Awaitable[str]] | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._auth_token_provider = auth_token_provider
        self._client: AsyncQdrantClient | None = None
        self._collection_ok = False

    async def _ensure_client(self) -> AsyncQdrantClient:
        if self._client is None:
            self._client = AsyncQdrantClient(
                url=self._url,
                api_key=self._api_key or None,
                auth_token_provider=self._auth_token_provider,
                timeout=30,
            )
        return self._client

    async def _ensure_collection(self) -> None:
        if self._collection_ok:
            return
        client = await self._ensure_client()
        try:
            info = await client.get_collection(COLLECTION_NAME)
            if info.status == "green":
                self._collection_ok = True
                return
        except Exception:
            pass

        try:
            await client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=COLLECTION_VECTORS,
                sparse_vectors_config=COLLECTION_SPARSE,
            )
            self._collection_ok = True
            logger.info(
                "Created Qdrant collection '%s' on %s", COLLECTION_NAME, self._url
            )
        except Exception as exc:
            logger.warning(
                "Qdrant create_collection failed (may already exist): %s", exc
            )
            self._collection_ok = True

    async def index_results(
        self,
        results: list[WebSearchResult],
        dense_embeddings: list[list[float]],
        sparse_embeddings: list[dict[str, list[int] | list[float]]],
    ) -> None:
        """Upsert final search results into the remote Qdrant index."""
        if not results or not dense_embeddings:
            return
        if len(results) != len(dense_embeddings) or len(results) != len(
            sparse_embeddings
        ):
            logger.warning(
                "index_results: embedding count mismatch (%d results, %d dense, %d sparse); skipping",
                len(results),
                len(dense_embeddings),
                len(sparse_embeddings),
            )
            return

        await self._ensure_collection()
        client = await self._ensure_client()
        now = datetime.now(timezone.utc).isoformat()

        points: list[models.PointStruct] = []
        for result, dense, sparse in zip(results, dense_embeddings, sparse_embeddings):
            url = result.link.strip()
            if not url:
                continue
            pid = _uuid_from_url(url)
            points.append(
                models.PointStruct(
                    id=pid,
                    vector={
                        "dense": dense,
                        "sparse": models.SparseVector(
                            indices=sparse["indices"],
                            values=sparse["values"],
                        ),
                    },
                    payload={
                        "url": url,
                        "title": result.title,
                        "snippet": result.snippet,
                        "domain": result.domain,
                        "resource_type": result.resource_type,
                        "score": result.score,
                        "provider_count": result.provider_count,
                        "indexed_at": now,
                    },
                )
            )

        if not points:
            return

        try:
            await client.upsert(
                collection_name=COLLECTION_NAME,
                points=points,
                wait=True,
            )
            logger.debug("Indexed %d results into %s", len(points), COLLECTION_NAME)
        except Exception as exc:
            logger.warning("Qdrant upsert failed for %d points: %s", len(points), exc)

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None


def get_web_results_index() -> WebResultsIndex | None:
    """Return singleton index client if indexing is enabled and URL is configured."""
    global _web_results_index
    if not settings.web_results_index_enabled:
        return None
    url = settings.qdrant_space_url.strip()
    if not url:
        logger.warning(
            "web_results_index_enabled=True but KINDLY_QDRANT_SPACE_URL is empty"
        )
        return None
    if _web_results_index is None:
        hf_token = settings.hf_token.strip() or None
        auth_provider: Callable[[], str] | None = (
            (lambda: hf_token) if hf_token else None
        )
        _web_results_index = WebResultsIndex(
            url=url,
            api_key=settings.qdrant_api_key.strip() or None,
            auth_token_provider=auth_provider,
        )
    return _web_results_index


async def index_final_results(
    query_text: str,
    results: list[WebSearchResult],
) -> None:
    """Compute embeddings + BM25 and index final search results into remote Qdrant.

    Intended to be called from the orchestrator after final_results are
    determined but before building the response. Errors are caught
    silently so they never propagate to the caller.
    """
    from ..embeddings import embed_texts
    from .bm25_encoder import encode_bm25

    idx = get_web_results_index()
    if idx is None:
        return

    if not results:
        return

    texts = [
        f"{r.title}\n{r.snippet}"
        if r.title and r.snippet
        else (r.title or r.snippet or "")
        for r in results
    ]
    texts = [t for t in texts if t.strip()]
    if not texts:
        return

    try:
        dense = await embed_texts(texts, timeout=15.0)
        if not dense:
            logger.debug("index_final_results: no dense embeddings returned")
            return

        sparse = [encode_bm25(t) for t in texts]
        await idx.index_results(results, dense, sparse)
        logger.debug(
            "index_final_results: indexed %d results for query=%s",
            len(results),
            query_text[:80],
        )
    except Exception:
        logger.debug("index_final_results: failed (non-fatal)", exc_info=True)
