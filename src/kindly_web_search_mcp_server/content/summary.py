from __future__ import annotations

import asyncio
import logging
from typing import Any, Sequence

from .summary_backend import summarize_with_fallback
from .summary_models import SummaryMode, summary_stub


logger = logging.getLogger(__name__)


async def create_summary(
    source_text: str,
    *,
    mode: SummaryMode,
    focus_query: str | None = None,
    source_urls: Sequence[str] | None = None,
) -> dict[str, Any] | None:
    if mode == "none":
        return None
    if not source_text.strip() and not source_urls:
        return summary_stub(mode)

    summary, _, _ = await summarize_with_fallback(
        source_text=source_text,
        source_urls=source_urls,
        mode=mode,
        focus_query=focus_query,
    )
    return summary


async def create_batch_summaries(
    items: Sequence[dict[str, Any]],
    *,
    mode: SummaryMode,
    focus_query: str | None = None,
    max_concurrency: int = 4,
) -> list[dict[str, Any] | None]:
    if mode == "none":
        return [None for _ in items]

    sem = asyncio.Semaphore(max(1, min(max_concurrency, 8)))

    async def _summarize(item: dict[str, Any]) -> dict[str, Any] | None:
        source_text = (item.get("page_content") or "").strip()
        source_url = item.get("fetched_url") or item.get("normalized_url") or item.get("input_url")
        try:
            async with sem:
                return await create_summary(
                    source_text,
                    mode=mode,
                    focus_query=focus_query,
                    source_urls=[source_url] if source_url else None,
                )
        except Exception as exc:
            logger.warning(
                "Batch summary failed for %s: %s",
                source_url or item.get("input_url") or "<unknown>",
                exc,
            )
            return None

    return await asyncio.gather(*(_summarize(item) for item in items))
