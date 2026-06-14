"""Fire-and-forget LLM judge evaluation for search pipeline runs.

Encapsulates the judge LLM call, response parsing, and analytics insert
so the pipeline can trigger it as a background task without blocking.
"""

from __future__ import annotations

import logging
from typing import Any

from ..settings import settings
from .duckdb_store import insert_judge_evaluation
from .search_relevance_judge import SearchRelevanceJudge

logger = logging.getLogger(__name__)

# Singleton judge instance
_judge_instance: SearchRelevanceJudge | None = None


def _get_judge() -> SearchRelevanceJudge:
    global _judge_instance
    if _judge_instance is None:
        _judge_instance = SearchRelevanceJudge()
    return _judge_instance


async def run_judge_evaluation(
    run_key: str,
    query: str,
    intent: str,
    results: list[Any],
    tool_name: str = "web_search",
    research_goal: str | None = None,
    rewrite_variants: list[Any] | None = None,
) -> None:
    """Evaluate search results relevance and persist scores.

    Designed to be called fire-and-forget (e.g. via ``asyncio.create_task``
    or wrapped in a try/except block).  This function handles all errors
    internally and will never propagate an exception to the caller.

    Parameters
    ----------
    run_key : str
        Unique identifier for the search pipeline run.
    query : str
        The original user query.
    intent : str
        Search intent: 'general', 'ai_coding', 'digital_humanities', or 'comparison'.
    results : list[WebSearchResult]
        The final result list returned to the user.
    tool_name : str
        Tool name for the analytics record (default 'web_search').
    research_goal : str | None
        The user's research goal, if provided.
    rewrite_variants : list[QueryVariant] | None
        Query rewrite variants used for multi-branch search.
    """
    if not settings.analytics_enabled:
        return

    if not results:
        logger.debug("judge evaluation skipped: no results")
        return

    judge = _get_judge()
    result = await judge.evaluate(
        query=query,
        intent=intent,
        results=results,
        research_goal=research_goal,
        rewrite_variants=rewrite_variants,
    )

    try:
        insert_judge_evaluation(
            run_key=run_key,
            tool_name=tool_name,
            judge_model=result.judge_model,
            relevance_score=result.relevance_score,
            relevance_raw=result.relevance_raw,
            relevance_scale="1-4",
            accuracy_score=None,
            completeness_score=None,
            source_quality_score=None,
            overall_score=result.relevance_score,
            rationale=result.reasoning,
            duration_ms=result.duration_ms,
            tokens_used=None,
            cost_usd=None,
            payload_json={
                "relevance_raw": result.relevance_raw,
                "relevance_scale": "1-4",
                "result_count": len(results),
                "error": result.error,
            },
        )
    except Exception as exc:
        logger.debug("insert_judge_evaluation failed: %s", exc)
