from __future__ import annotations

from typing import Any

from ...content.batch_orchestrator import BatchParams, run_batch_fetch
from ...content.options import build_fetch_options


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
) -> dict[str, Any]:
    fetch_options = build_fetch_options(
        include_metadata=include_metadata,
        include_links=include_links,
        max_links=max_links,
        strip_selectors=strip_selectors,
    )
    return await run_batch_fetch(
        urls=urls,
        params=BatchParams(
            max_concurrency=max_concurrency,
            per_item_char_length=per_item_char_length,
            total_char_budget=total_char_budget,
            per_url_timeout_seconds=per_url_timeout_seconds,
        ),
        cursor=cursor,
        fetch_options=fetch_options,
    )

