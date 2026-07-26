"""Cohere rerank provider adapter."""

from __future__ import annotations

import json
from typing import Any

from ..registry import ProviderAdapter, register_provider_adapter
from ..types import LLMGeneration, ModelCapability, ModelSpec


async def execute_cohere_rerank(
    spec: ModelSpec,
    *,
    messages: list[dict[str, str]] | None = None,
    documents: list[str] | None = None,
    query: str | None = None,
    top_n: int | None = None,
    **kwargs: Any,
) -> LLMGeneration:
    import httpx
    from ..types import LLMUsage

    model = spec.model_id
    async with httpx.AsyncClient(timeout=spec.default_timeout) as client:
        body: dict[str, Any] = {"model": model}
        if query is not None:
            body["query"] = query
        if documents is not None:
            body["documents"] = documents
        if top_n is not None:
            body["top_n"] = top_n
        base_url = spec.base_url or "https://api.cohere.com/v2/rerank"
        response = await client.post(
            base_url,
            headers={"Authorization": f"Bearer {spec.api_key}"},
            json=body,
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("results", [])

    result_text = json.dumps(results)
    return LLMGeneration(
        spec=spec,
        content=result_text,
        usage=LLMUsage(
            input_tokens=data.get("usage", {}).get("input_tokens"),
            output_tokens=data.get("usage", {}).get("output_tokens"),
        ),
    )


def _init() -> None:
    register_provider_adapter(
        ProviderAdapter(
            name="cohere",
            execute=execute_cohere_rerank,
            capabilities=frozenset({ModelCapability.RERANK}),
        )
    )


_init()
