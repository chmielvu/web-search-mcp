from __future__ import annotations

from typing import Any

from ...cache import get_page_cache
from ...content.batch_orchestrator import BatchParams, run_batch_fetch
from ...content.options import build_fetch_options
from ...content.summary import create_batch_summaries
from ...utils.url_canonicalize import canonicalize_url


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

    safe_summary_mode = summary_mode if summary_mode in {"none", "brief", "detailed"} else "none"
    summaries = await create_batch_summaries(
        output["results"],
        mode=safe_summary_mode,  # type: ignore[arg-type]
        focus_query=focus_query,
        max_concurrency=max_concurrency,
    )
    results: list[dict[str, Any]] = []
    for idx, item in enumerate(output["results"]):
        result = {
            **item,
            "summary": summaries[idx],
            "content_quality": item["status"],
            "content_word_count": item.get("word_count") or len(item["page_content"].split()),
        }
        results.append(result)

        # Cache the full page only when the window captured it completely.
        window = item.get("window") or {}
        if (
            item.get("status") == "success"
            and item.get("page_content")
            and window.get("offset", 0) == 0
            and not window.get("has_more", False)
        ):
            try:
                await get_page_cache().astore(
                    canonical_url=canonicalize_url(item["input_url"]),
                    page_content=item["page_content"],
                    extraction_method=item.get("fetch_backend") or "batch",
                    metadata={
                        "metadata": item.get("metadata"),
                        "links": item.get("links"),
                    },
                )
            except Exception:
                pass

    return {
        **output,
        "results": results,
    }
