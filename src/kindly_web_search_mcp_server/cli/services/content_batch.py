from __future__ import annotations

from typing import Any

from ...content.batch_orchestrator import BatchParams, run_batch_fetch
from ...content.options import build_fetch_options
from ...content.summary import create_batch_summaries


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
    summary_mode: str = "none",
    focus_query: str | None = None,
) -> dict[str, Any]:
    fetch_options = build_fetch_options(
        include_metadata=include_metadata,
        include_links=include_links,
        max_links=max_links,
        strip_selectors=strip_selectors,
    )
    output = await run_batch_fetch(
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

    safe_summary_mode = (
        summary_mode if summary_mode in {"none", "brief", "detailed"} else "none"
    )
    summaries = await create_batch_summaries(
        output["results"],
        mode=safe_summary_mode,
        focus_query=focus_query,
        max_concurrency=max_concurrency,
    )
    return {
        **output,
        "results": [
            {**item, "summary": summaries[idx]}
            for idx, item in enumerate(output["results"])
        ],
    }
