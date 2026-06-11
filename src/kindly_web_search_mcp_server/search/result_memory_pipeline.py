"""Result-memory candidate injection and persistence helpers."""

from __future__ import annotations

import logging
from typing import Any

from ..cache.result_memory import get_result_memory_store
from ..embeddings import embed_query
from ..models import WebSearchResult
from ..settings import settings
from ..utils.observability import emit_observability_event

logger = logging.getLogger(__name__)


def _candidate_fields(candidate: Any) -> tuple[str, str, str]:
    if isinstance(candidate, dict):
        return (
            candidate.get("url") or candidate.get("link", ""),
            candidate.get("title", ""),
            candidate.get("snippet", ""),
        )
    return (
        getattr(candidate, "url", getattr(candidate, "link", "")),
        getattr(candidate, "title", ""),
        getattr(candidate, "snippet", ""),
    )


async def inject_result_memory_candidates(
    *,
    query: str,
    normalized_query: str,
    result_lists: list[list[WebSearchResult]],
    list_weights: list[float],
    get_result_memory_store_fn=get_result_memory_store,
    embed_query_fn=embed_query,
) -> tuple[
    list[list[WebSearchResult]],
    list[float],
    list[float] | None,
    list[WebSearchResult],
]:
    if not settings.result_memory_enabled:
        return result_lists, list_weights, None, []

    try:
        import kindly_web_search_mcp_server.cache.result_memory as _rm_mod

        if hasattr(_rm_mod, "_result_memory_store"):
            _rm_mod._result_memory_store = None
        memory = get_result_memory_store_fn()
        query_embedding = await embed_query_fn(
            normalized_query, timeout=15.0, skip_circuit_check=True
        )
        raw_candidates = memory.lookup_candidates(
            query_embedding=query_embedding,
            limit=settings.result_memory_candidate_limit,
            min_similarity=settings.result_memory_min_similarity,
        )
        if not raw_candidates:
            return result_lists, list_weights, query_embedding, []

        injected: list[WebSearchResult] = []
        for candidate in raw_candidates:
            url, title, snippet = _candidate_fields(candidate)
            if not url:
                continue
            injected.append(
                WebSearchResult(
                    title=title,
                    link=url,
                    snippet=snippet,
                    providers=["result_memory"],
                    raw_score=0.0,
                )
            )
        if injected:
            emit_observability_event(
                logger,
                "result_memory.candidate_injected",
                query=query,
                count=len(injected),
                weight=settings.result_memory_candidate_weight,
            )
            return (
                list(result_lists) + [injected],
                list(list_weights) + [settings.result_memory_candidate_weight],
                query_embedding,
                injected,
            )
        return result_lists, list_weights, query_embedding, []
    except Exception as exc:
        logger.warning("Result memory lookup/injection failed (non-fatal): %s", exc)
        return result_lists, list_weights, None, []


async def store_result_memory_results(
    *,
    normalized_query: str,
    results: list[WebSearchResult],
    query_embedding: list[float] | None,
    get_result_memory_store_fn=get_result_memory_store,
    embed_query_fn=embed_query,
) -> None:
    if not settings.result_memory_enabled:
        return

    try:
        import kindly_web_search_mcp_server.cache.result_memory as _rm_mod

        if hasattr(_rm_mod, "_result_memory_store"):
            _rm_mod._result_memory_store = None
        memory = get_result_memory_store_fn()
        if query_embedding is None:
            query_embedding = await embed_query_fn(
                normalized_query, timeout=15.0, skip_circuit_check=True
            )
        memory.store_results(
            query_text=normalized_query,
            query_embedding=query_embedding,
            results=[
                {
                    "title": result.title,
                    "link": result.link,
                    "snippet": result.snippet,
                    "providers": result.providers or [],
                }
                for result in results
            ],
        )
        emit_observability_event(
            logger,
            "result_memory.store",
            query=normalized_query,
            count=len(results),
        )
    except Exception as exc:
        logger.warning("Result memory store failed (non-fatal): %s", exc)
