"""LLM query rewrite for the Parallel Search API web mode.

Transforms caller-supplied queries into four API-tuned queries (original,
refined, two decomposed angles) before dispatch, using the shared inference
router. Any failure falls open to the original queries; the quick web search
tool never blocks on this stage.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from ...inference.router import build_worker_router
from ...prompts.query_rewrite_parallel import (
    PARALLEL_REWRITE_PROMPT_VERSION,
    PARALLEL_REWRITE_SYSTEM,
    PARALLEL_REWRITE_USER,
    ParallelRewrittenQueries,
)

LOGGER = logging.getLogger(__name__)

_MAX_QUERY_CHARS = 200
_REWRITE_TIMEOUT_SECONDS = 20.0


def _clip_query(value: str) -> str:
    """Collapse whitespace and trim to the Parallel API 200-character bound."""
    text = " ".join(value.split())
    if len(text) <= _MAX_QUERY_CHARS:
        return text
    clipped = text[:_MAX_QUERY_CHARS]
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    return clipped


async def rewrite_queries_for_parallel(
    search_queries: list[str],
    objective: str,
) -> tuple[list[str], dict[str, Any]]:
    """Rewrite caller queries into four Parallel API-tuned queries.

    Args:
        search_queries: Non-empty caller queries (validated upstream).
        objective: Natural-language goal providing intent context.

    Returns:
        Tuple of (executed queries, rewrite metadata). The executed list keeps
        the original slot, the refined slot, and both decomposed slots,
        deduplicated and clipped to 200 characters. On any failure the original
        queries are returned unchanged with an ``error`` metadata key.
    """
    original = [query for query in (query.strip() for query in search_queries) if query]
    if not original:
        return [], {"error": "empty_input"}
    started = time.monotonic()
    try:
        user_content = PARALLEL_REWRITE_USER.format(
            current_year=datetime.now(tz=UTC).year,
            input_queries=original,
            objective=objective.strip(),
        )
        generation = await build_worker_router().complete_json(
            messages=[
                {"role": "system", "content": PARALLEL_REWRITE_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            response_model=ParallelRewrittenQueries,
            timeout_seconds=_REWRITE_TIMEOUT_SECONDS,
            reasoning_effort="none",
            operation="rewrite",
        )
        parsed = ParallelRewrittenQueries.model_validate_json(generation.content)
    except Exception as exc:
        LOGGER.warning(
            "Parallel query rewrite failed; using original queries: %s", type(exc).__name__
        )
        return list(original), {"error": type(exc).__name__}

    primary = parsed.original.strip() or original[0]
    candidates = [
        primary,
        parsed.refined.strip() or primary,
        parsed.decomposed_1.strip(),
        parsed.decomposed_2.strip(),
    ]
    executed: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        clipped = _clip_query(candidate)
        key = clipped.casefold()
        if not clipped or key in seen:
            continue
        seen.add(key)
        executed.append(clipped)
    metadata = {
        "model": generation.model_used,
        "input_tokens": generation.input_tokens,
        "output_tokens": generation.output_tokens,
        "latency_ms": (time.monotonic() - started) * 1000.0,
        "prompt_version": PARALLEL_REWRITE_PROMPT_VERSION,
    }
    if not executed:
        return list(original), {**metadata, "error": "empty_rewrite"}
    return executed, metadata
