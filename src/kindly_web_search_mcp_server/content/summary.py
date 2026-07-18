from __future__ import annotations

import logging
from typing import Any, Sequence

from .summary_backend import summarize_batch_with_fallback, summarize_with_fallback
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

    if not items:
        return []

    return await summarize_batch_with_fallback(
        items=items,
        mode=mode,
        focus_query=focus_query,
    )
