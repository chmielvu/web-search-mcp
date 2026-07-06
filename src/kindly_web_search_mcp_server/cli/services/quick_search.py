from __future__ import annotations

from typing import Any

from ...composio_tools import _quick_web_search_impl


async def fetch_quick_web_search_payload(query: str) -> dict[str, Any]:
    response = await _quick_web_search_impl(query)
    return response.model_dump(exclude_none=True)
