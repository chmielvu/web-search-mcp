"""Composio web-search provider for the shared web_search mix."""

from __future__ import annotations

from typing import Any

from ..composio_client import execute_composio_tool
from ..composio_tools import WEB_SEARCH_SLUG
from ..models import WebSearchResult
from .base_provider import run_clientless_provider

COMPOSIO_LLM_SEARCH_SLUG = WEB_SEARCH_SLUG


class ComposioLLMSearchError(RuntimeError):
    """Composio web-search provider error."""


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


def _extract_result_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    results_container = data.get("results", data)
    if isinstance(results_container, list):
        return results_container
    if not isinstance(results_container, dict):
        raise ComposioLLMSearchError(
            "Composio LLM Search response was not a results object."
        )

    citations = results_container.get("citations")
    if isinstance(citations, list):
        return citations

    raw_results = results_container.get("results")
    if isinstance(raw_results, list):
        return raw_results

    raise ComposioLLMSearchError(
        "Composio LLM Search response missing `citations` or `results` list."
    )


async def search_composio_llm_search(
    query: str,
    *,
    num_results: int,
    http_client: Any = None,
    cancel_token: Any = None,
) -> list[WebSearchResult]:
    """Query Composio web search and return lightweight provider records."""
    if not query.strip() or num_results < 1:
        return []

    async def _request() -> dict[str, Any]:
        return await execute_composio_tool(
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
            cancel_token=cancel_token,
        )

    def _parse_response(data: dict[str, Any]) -> list[WebSearchResult]:
        results: list[WebSearchResult] = []
        for item in _extract_result_items(data):
            if not isinstance(item, dict):
                continue
            title = item.get("title") or item.get("name")
            link = item.get("url") or item.get("link")
            snippet = item.get("snippet") or item.get("content") or ""
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
                )
            )
            if len(results) >= num_results:
                break
        return results

    return await run_clientless_provider(
        "composio_llm_search",
        query,
        num_results,
        request=_request,
        parse_response=_parse_response,
    )
