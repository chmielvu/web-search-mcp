from __future__ import annotations

from typing import Any

from ...search.gemini_search_tool import gemini_search_with_grounding
from ...search.grok import grok_search
from ...search.pollinations import get_pollinations_client


async def fetch_gemini_search_payload(
    query: str,
    *,
    structured_output: bool,
    research_goal: str | None,
) -> dict[str, Any]:
    response = await gemini_search_with_grounding(
        query,
        structured_output=structured_output,
        research_goal=research_goal,
    )
    payload = response.model_dump(exclude_none=True)
    payload.pop("search_widget_html", None)
    return payload


async def fetch_perplexity_search_payload(
    query: str,
    *,
    depth: str,
    research_goal: str | None,
) -> dict[str, Any]:
    client = get_pollinations_client()
    result = await client.web_search(query, depth=depth, research_goal=research_goal)
    return {
        "query": query,
        "answer": result.get("answer"),
        "sources": result.get("sources", []),
        "model": result.get("model"),
        "error": None,
    }


async def fetch_grok_search_payload(
    query: str,
    *,
    research_goal: str,
    model: str | None,
    num_results: int,
    allowed_domains: list[str] | None,
    excluded_domains: list[str] | None,
    timeout: float | None,
) -> dict[str, Any]:
    result = await grok_search(
        query,
        research_goal,
        model=model,
        num_results=num_results,
        allowed_domains=allowed_domains,
        excluded_domains=excluded_domains,
        timeout=timeout,
    )
    payload = result.__dict__.copy()
    payload.setdefault("error", None)
    return payload

