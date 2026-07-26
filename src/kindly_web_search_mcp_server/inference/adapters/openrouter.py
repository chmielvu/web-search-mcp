"""OpenRouter rerank provider adapter."""

from __future__ import annotations

import json
from typing import Any

from ..registry import ProviderAdapter, register_provider_adapter
from ..types import LLMGeneration, ModelCapability, ModelSpec


async def execute_openrouter_rerank(
    spec: ModelSpec,
    *,
    query: str | None = None,
    documents: list[str] | None = None,
    top_n: int | None = None,
    **kwargs: Any,
) -> LLMGeneration:
    import httpx

    async with httpx.AsyncClient(timeout=spec.default_timeout) as client:
        body: dict[str, Any] = {"model": spec.model_id}
        if query is not None:
            body["query"] = query
        if documents is not None:
            body["documents"] = documents
        if top_n is not None:
            body["top_n"] = top_n
        base_url = spec.base_url or "https://openrouter.ai/api/v1/rerank"
        response = await client.post(
            base_url,
            headers={"Authorization": f"Bearer {spec.api_key}"},
            json=body,
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("results", [])

    content = json.dumps(results)
    return LLMGeneration(
        spec=spec,
        content=content,
        usage=None,
    )


def _init() -> None:
    register_provider_adapter(
        ProviderAdapter(
            name="openrouter_rerank",
            execute=execute_openrouter_rerank,
            capabilities=frozenset({ModelCapability.RERANK}),
        )
    )


_init()
