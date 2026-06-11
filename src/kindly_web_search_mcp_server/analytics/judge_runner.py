"""Fire-and-forget LLM judge evaluation for search pipeline runs.

Encapsulates the judge LLM call, response parsing, and analytics insert
so the pipeline can trigger it as a background task without blocking.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from litellm import acompletion

from ..settings import settings
from .duckdb_store import insert_judge_evaluation
from .judge_prompt import (
    JUDGE_SYSTEM_PROMPT,
    build_judge_user_prompt,
    parse_judge_response,
)

logger = logging.getLogger(__name__)


def _format_results_text(results: list[Any]) -> str:
    """Build a compact text representation of search results for the judge."""
    lines: list[str] = []
    for i, r in enumerate(results, start=1):
        title = getattr(r, "title", "") or ""
        link = getattr(r, "link", "") or ""
        snippet = getattr(r, "snippet", "") or ""
        lines.append(f"[{i}] {title}\n    URL: {link}\n    Snippet: {snippet}\n")
    return "\n".join(lines) if lines else "(no results returned)"


async def _call_judge_llm(
    system_prompt: str,
    user_prompt: str,
) -> str:
    """Make a single OpenAI-compatible chat completion for the judge."""
    model = settings.judge_model
    api_base: str | None = None
    api_key: str | None = None

    # If Vercel AI Gateway credentials are available, route through it.
    # Otherwise let litellm resolve the provider natively.
    if settings.vercel_ai_gateway_api_key:
        api_base = settings.vercel_ai_gateway_base_url
        api_key = settings.vercel_ai_gateway_api_key

    response = await acompletion(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        api_base=api_base,
        api_key=api_key,
        timeout=settings.judge_timeout_seconds,
    )
    content = response.choices[0].message.content or ""
    return content.strip()


async def run_judge_evaluation(
    run_key: str,
    query: str,
    intent: str,
    results: list[Any],
    tool_name: str = "web_search",
) -> None:
    """Evaluate search results with an LLM judge and persist scores.

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
        Inferred search intent (e.g. 'informational', 'navigational').
    results : list[WebSearchResult]
        The final result list returned to the user.
    tool_name : str
        Tool name for the analytics record (default 'web_search').
    """
    if not results:
        logger.debug("judge evaluation skipped: no results to evaluate")
        return

    judge_start = asyncio.get_event_loop().time()
    results_text = _format_results_text(results)

    try:
        user_prompt = build_judge_user_prompt(
            query=query,
            intent=intent,
            results_text=results_text,
            tool_name=tool_name,
        )

        raw_response = await _call_judge_llm(
            system_prompt=JUDGE_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
    except Exception as exc:
        logger.debug("judge LLM call failed: %s", exc)
        # Still record a best-effort row so the run shows up in analytics
        _insert_fallback(run_key, tool_name, error=str(exc)[:500])
        return

    if not raw_response:
        logger.debug("judge returned empty response")
        _insert_fallback(run_key, tool_name, error="empty response")
        return

    parsed = parse_judge_response(raw_response)

    duration_ms = round(
        (asyncio.get_event_loop().time() - judge_start) * 1000.0, 3
    )

    try:
        insert_judge_evaluation(
            run_key=run_key,
            tool_name=tool_name,
            judge_model=settings.judge_model,
            relevance_score=parsed.get("relevance_score"),
            accuracy_score=parsed.get("accuracy_score"),
            completeness_score=parsed.get("completeness_score"),
            source_quality_score=parsed.get("source_quality_score"),
            overall_score=parsed.get("overall_score"),
            rationale=parsed.get("rationale"),
            duration_ms=duration_ms,
            tokens_used=None,
            cost_usd=None,
            payload_json={
                "scores_raw": {k: v for k, v in parsed.items() if v is not None},
                "result_count": len(results),
            },
        )
    except Exception as exc:
        logger.debug("insert_judge_evaluation failed: %s", exc)


def _insert_fallback(
    run_key: str,
    tool_name: str,
    error: str,
) -> None:
    """Insert a fallback row when the judge call itself failed."""
    try:
        insert_judge_evaluation(
            run_key=run_key,
            tool_name=tool_name,
            judge_model=settings.judge_model,
            relevance_score=None,
            accuracy_score=None,
            completeness_score=None,
            source_quality_score=None,
            overall_score=None,
            rationale=None,
            duration_ms=None,
            tokens_used=None,
            cost_usd=None,
            payload_json={"error": error},
        )
    except Exception as exc:
        logger.debug("insert_judge_evaluation (fallback) failed: %s", exc)