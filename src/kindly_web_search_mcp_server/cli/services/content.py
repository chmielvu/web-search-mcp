"""CLI adapter for the unified MCP fetch tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from ...tools.content import fetch
from ...models import FetchResponse


async def fetch_payload(
    *,
    urls: list[str] | None,
    cursor: str | None = None,
    offset: int = 0,
    ai_summary: bool = False,
    focus_query: str | None = None,
    include_metadata: bool = True,
    include_links: bool = False,
    max_links: int = 25,
    strip_selectors: str | None = None,
) -> dict[str, Any]:
    """Call the unified fetch tool without exposing resource tuning knobs."""
    mock_ctx = AsyncMock()
    mock_ctx.info = AsyncMock()
    mock_ctx.report_progress = AsyncMock()

    values = [item.strip() for item in (urls or []) if item.strip()]
    primary = values[0] if values else None
    additional = values[1:] if len(values) > 1 else None
    output = await fetch(
        url=primary,
        urls=additional,
        offset=offset,
        cursor=cursor,
        ai_summary=ai_summary,
        focus_query=focus_query,
        include_metadata=include_metadata,
        include_links=include_links,
        max_links=max_links,
        strip_selectors=strip_selectors,
        ctx=mock_ctx,
    )
    return output.model_dump(exclude_none=True) if isinstance(output, FetchResponse) else dict(output)
