from __future__ import annotations

from typing import Any

from ...quick_web_search import _quick_web_search_impl


async def fetch_quick_web_search_payload(
    search_queries: list[str], objective: str
) -> dict[str, Any]:
    response = await _quick_web_search_impl(search_queries, objective=objective)
    return response.model_dump(exclude_none=True)
