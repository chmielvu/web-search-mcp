"""Voyage AI rerank provider adapter."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from ..registry import ProviderAdapter, register_provider_adapter
from ..types import LLMGeneration, LLMUsage, ModelCapability, ModelSpec


def _compose_voyage_query(query: str, instruction: str | None) -> str:
    """Prefix standing instructions using Voyage's documented layout."""
    if instruction and instruction.strip():
        return f"{instruction.strip()}\nQuery: {query}"
    return query


async def execute_voyage_rerank(
    spec: ModelSpec,
    *,
    query: str | None = None,
    documents: list[str] | None = None,
    top_n: int | None = None,
    instruction: str | None = None,
    **kwargs: Any,
) -> LLMGeneration:
    import voyageai

    del kwargs
    composed_query = _compose_voyage_query(query or "", instruction)

    def _rerank() -> Any:
        client = voyageai.Client(
            api_key=spec.api_key,
            max_retries=0,
            timeout=spec.default_timeout,
        )
        return client.rerank(
            query=composed_query,
            documents=list(documents or []),
            model=spec.model_id,
            top_k=top_n,
            truncation=True,
        )

    ranking = await asyncio.to_thread(_rerank)
    results = [
        {"index": item.index, "relevance_score": item.relevance_score} for item in ranking.results
    ]
    total_tokens = getattr(ranking, "total_tokens", None)
    usage = LLMUsage(total_tokens=total_tokens) if isinstance(total_tokens, int) else None
    return LLMGeneration(
        spec=spec,
        content=json.dumps(results),
        usage=usage,
    )


def _init() -> None:
    register_provider_adapter(
        ProviderAdapter(
            name="voyage",
            execute=execute_voyage_rerank,
            capabilities=frozenset({ModelCapability.RERANK}),
        )
    )


_init()
