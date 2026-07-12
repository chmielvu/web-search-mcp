"""Fire-and-forget LLM judge evaluation for search pipeline runs.

Encapsulates the judge LLM call, response parsing, and analytics insert
so the pipeline can trigger it as a background task without blocking.
"""

from __future__ import annotations

import logging
from typing import Any

from ..settings import settings
from ..llm.phoenix_tracing import LLMTraceContext
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
    session_id: str | None = None,
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
        Search intent: 'general', 'ai_coding_and_infrastructure',
        'digital_humanities', 'comparison', 'social_media', or 'news'.
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
    langfuse_trace = LLMTraceContext(
        trace_name=f"judge:{tool_name}",
        session_id=session_id or run_key,
        metadata={
            "task": "judge",
            "run_key": run_key,
            "tool_name": tool_name,
            "intent": intent,
            "research_goal": research_goal or "",
        },
    )
    result = await judge.evaluate(
        query=query,
        intent=intent,
        results=results,
        research_goal=research_goal,
        rewrite_variants=rewrite_variants,
        langfuse=langfuse_trace,
    )

    try:
        tokens_used = None
        if result.input_tokens is not None or result.output_tokens is not None:
            tokens_used = (result.input_tokens or 0) + (result.output_tokens or 0)
        insert_judge_evaluation(
            run_key=run_key,
            tool_name=tool_name,
            judge_model=result.judge_model,
            model_used=result.model_used,
            link=None,  # judge evaluates the full result set, not one URL
            relevance_grade=result.relevance_grade,
            relevance_score=result.relevance_score,
            accuracy_grade=result.accuracy_grade,
            accuracy_score=result.accuracy_score,
            completeness_grade=result.completeness_grade,
            completeness_score=result.completeness_score,
            source_quality_grade=result.source_quality_grade,
            source_quality_score=result.source_quality_score,
            overall_score=result.overall_score,
            rationale=result.rationale,
            duration_ms=result.duration_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            tokens_used=tokens_used,
            cost_usd=None,
            payload_json={
                "result_count": len(results),
                "error": result.error,
            },
        )
    except Exception as exc:
        logger.debug("insert_judge_evaluation failed: %s", exc)
