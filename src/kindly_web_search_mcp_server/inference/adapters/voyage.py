"""Voyage AI rerank provider adapter."""

from __future__ import annotations

import json
from typing import Any

from ..registry import ProviderAdapter, register_provider_adapter
from ..types import LLMGeneration, ModelCapability, ModelSpec


def _format_voyage_query(query: str) -> str:
    """Move the compact cross query into Voyage's instruction-first layout."""
    if " | " not in query and "Research goal:" not in query:
        return query

    parts = query.split(" | ", 4)
    if len(parts) < 4:
        return query

    user_query, research_goal, intent, policy = (part.strip() for part in parts[:4])
    instruction_lines = [research_goal, intent, policy]
    if len(parts) == 5 and parts[4].strip():
        instruction_lines.append(parts[4].strip())
    return "\n".join(instruction_lines) + f"\n\nQuery: {user_query}"


async def execute_voyage_rerank(
    spec: ModelSpec,
    *,
    query: str | None = None,
    documents: list[str] | None = None,
    top_n: int | None = None,
    instruction: str | None = None,
    **kwargs: Any,
) -> LLMGeneration:
    import httpx

    async with httpx.AsyncClient(timeout=spec.default_timeout) as client:
        body: dict[str, Any] = {"model": spec.model_id}
        if query is not None:
            if instruction and instruction.strip():
                body["query"] = f"{instruction.strip()}\n\n{query}"
            else:
                body["query"] = _format_voyage_query(query)
        if documents is not None:
            body["documents"] = documents
        if top_n is not None:
            body["top_k"] = top_n
        base_url = spec.base_url or "https://api.voyageai.com/v1/rerank"
        response = await client.post(
            base_url,
            headers={"Authorization": f"Bearer {spec.api_key}"},
            json=body,
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("data", [])

    content = json.dumps(results)
    return LLMGeneration(
        spec=spec,
        content=content,
        usage=None,
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
