"""CLI batch content fetch — delegates to the MCP tool layer."""

from __future__ import annotations

from typing import Any

from unittest.mock import AsyncMock

from ...tools.content import batch_get_content


async def fetch_batch_content_payload(
    *,
    urls: list[str] | None,
    cursor: str | None,
    max_concurrency: int,
    per_item_char_length: int,
    total_char_budget: int,
    per_url_timeout_seconds: float,
    include_metadata: bool,
    include_links: bool,
    max_links: int,
    strip_selectors: str | None,
    ai_summary: bool = False,
    focus_query: str | None = None,
) -> dict[str, Any]:
    # The CLI layer bypasses FastMCP, so we supply a minimal mock context
    # that satisfies get_content's ctx.info / ctx.report_progress calls.
    mock_ctx = AsyncMock()
    mock_ctx.info = AsyncMock()
    mock_ctx.report_progress = AsyncMock()

    output = await batch_get_content(
        urls=urls,
        max_concurrency=max_concurrency,
        per_item_char_length=per_item_char_length,
        total_char_budget=total_char_budget,
        cursor=cursor,
        ai_summary=ai_summary,
        focus_query=focus_query,
        include_metadata=include_metadata,
        include_links=include_links,
        max_links=max_links,
        strip_selectors=strip_selectors,
        ctx=mock_ctx,
    )

    return dict(output)
