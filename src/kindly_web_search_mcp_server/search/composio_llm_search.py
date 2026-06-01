"""Composio LLM Search provider for the shared web_search mix."""

from __future__ import annotations

from typing import Any

from ..composio_client import execute_composio_tool
from ..models import WebSearchResult

COMPOSIO_LLM_SEARCH_SLUG = "COMPOSIO_SEARCH_TAVILY"


class ComposioLLMSearchError(RuntimeError):
    """Composio LLM Search provider error."""


def _resolve_timeout_seconds(http_client: Any) -> float | None:
    """Use shared HTTP client timeout budget when available."""
    if http_client is None:
        return None
    timeout_obj = getattr(http_client, "timeout", None)
    if timeout_obj is None:
        return None
    connect = getattr(timeout_obj, "connect", None)
    read = getattr(timeout_obj, "read", None)
    write = getattr(timeout_obj, "write", None)
    pool = getattr(timeout_obj, "pool", None)
    candidates = [
        value
        for value in (connect, read, write, pool)
        if isinstance(value, (int, float))
    ]
    if not candidates:
        return None
    return float(max(candidates))


async def search_composio_llm_search(
    query: str,
    *,
    num_results: int,
    http_client: Any = None,
) -> list[WebSearchResult]:
    """Query Composio LLM Search and return lightweight provider records."""
    if not query.strip() or num_results < 1:
        return []

    data = await execute_composio_tool(
        COMPOSIO_LLM_SEARCH_SLUG,
        {
            "query": query,
            "max_results": int(num_results),
            "search_depth": "basic",
            "include_answer": False,
            "include_images": False,
            "include_raw_content": False,
        },
        timeout_seconds=_resolve_timeout_seconds(http_client),
    )

    raw_results = data.get("results", [])
    if not isinstance(raw_results, list):
        raise ComposioLLMSearchError(
            "Composio LLM Search response missing `results` list."
        )

    results: list[WebSearchResult] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        link = item.get("url")
        snippet = item.get("content")
        if not isinstance(title, str) or not title.strip():
            continue
        if not isinstance(link, str) or not link.strip():
            continue
        if not isinstance(snippet, str):
            snippet = ""
        results.append(
            WebSearchResult(
                title=title.strip(),
                link=link.strip(),
                snippet=snippet.strip(),
                providers=["composio_llm_search"],
            )
        )
        if len(results) >= num_results:
            break
    return results
