from __future__ import annotations

import logging
from typing import Any, Sequence, cast

from .summary_backend import summarize_batch_with_fallback, summarize_with_fallback
from .summary_models import summary_stub


logger = logging.getLogger(__name__)


async def create_summary(
    source_text: str,
    *,
    ai_summary: bool = False,
    focus_query: str | None = None,
    source_urls: Sequence[str] | None = None,
) -> dict[str, Any] | None:
    if not ai_summary:
        return None
    if not source_text.strip() and not source_urls:
        return summary_stub("detailed")

    summary, _, _ = await summarize_with_fallback(
        source_text=source_text,
        source_urls=source_urls,
        mode="detailed",
        focus_query=focus_query,
    )
    return summary


async def create_batch_summaries(
    items: Sequence[dict[str, Any]],
    *,
    ai_summary: bool = False,
    focus_query: str | None = None,
    max_concurrency: int = 4,
) -> list[dict[str, Any] | None]:
    if not ai_summary:
        return [None for _ in items]

    if not items:
        return []

    return cast(
        list[dict[str, Any] | None],
        await summarize_batch_with_fallback(
            items=items,
            mode="detailed",
            focus_query=focus_query,
        ),
    )
