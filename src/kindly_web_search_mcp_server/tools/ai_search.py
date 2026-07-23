from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import httpx
from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

from ..analytics.judge_runner import run_judge_evaluation
from ..errors import format_tool_error
from ..search.gemini_search_tool import gemini_search_with_grounding_dual
from ..search.providers.grok import grok_search as _grok_search_core
from ..settings import settings
from ..telemetry import record_gemini_search
from ..utils.background_tasks import fire_and_forget
from ..utils.observability import emit_tool_observability_event
from ._helpers import _record_tool_failure, _record_tool_success, _resolve_session_id

LOGGER = logging.getLogger(__name__)


async def gemini_search(
    query: str,
    structured_output: bool = False,
    research_goal: str | None = None,
    ctx: Context = CurrentContext(),
) -> dict:
    """AI-powered search synthesis grounded with real-time Google Search results.

    When to use this tool:
    - For quick factual lookups, current event summaries, and direct AI-synthesized answers.
    - When you need inline grounding citations [N] without manually fetching multiple URLs yourself.

    Key constraints:
    - Do NOT use when you need to inspect raw source pages yourself (use web_search + get_content).
    - Do NOT call this tool more than 3 times per question.

    Args:
        query: The search query string.
        structured_output: When True, generates schema-guided structured results
            alongside the narrative answer. Useful for data extraction tasks.
        research_goal: Optional description of what you intend to learn.
            Helps the grounding model focus on relevant sources.
    """
    emit_tool_observability_event(
        LOGGER,
        "gemini_search",
        "request",
        query=query,
        structured_output=structured_output,
        research_goal=research_goal,
    )

    start_time = time.time()
    await ctx.report_progress(progress=10, total=100, message="Querying Gemini with grounding...")
    try:
        result = await gemini_search_with_grounding_dual(
            query, structured_output=structured_output, research_goal=research_goal
        )
        response = result
        response.pop("search_widget_html", None) if isinstance(
            response.get("search_widget_html"), str
        ) else None
        duration_seconds = time.time() - start_time

        # Record Gemini search telemetry from overview branch
        overview = response.get("overview", {})
        deepdive = response.get("deepdive", {})
        grounding_queries_ov = len(overview.get("web_search_queries", []))
        grounding_queries_dd = len(deepdive.get("web_search_queries", []))
        grounding_chunks_ov = len(overview.get("grounding_chunks", []))
        grounding_chunks_dd = len(deepdive.get("grounding_chunks", []))
        record_gemini_search(
            grounding_queries=grounding_queries_ov + grounding_queries_dd,
            grounding_chunks=grounding_chunks_ov + grounding_chunks_dd,
            structured_output=structured_output,
            duration_seconds=duration_seconds,
        )

        emit_tool_observability_event(
            LOGGER,
            "gemini_search",
            "response",
            query=query,
            structured_output=structured_output,
            research_goal=research_goal,
            overview_answer=overview.get("answer"),
            deepdive_answer=deepdive.get("answer"),
            model_used=response.get("model_used"),
            both_succeeded=response.get("both_succeeded"),
            overview_input_tokens=overview.get("input_tokens"),
            deepdive_input_tokens=deepdive.get("input_tokens"),
            overview_web_search_queries=overview.get("web_search_queries", []),
            deepdive_web_search_queries=deepdive.get("web_search_queries", []),
            error=response.get("error"),
        )
        _record_tool_success(
            "gemini_search",
            input_query=query,
            output_content=overview.get("answer")
            if isinstance(overview.get("answer"), str)
            else str(overview.get("answer", "")),
        )
        await ctx.report_progress(progress=100, total=100, message="Done")
        if settings.judge_evaluation_enabled:
            try:
                _run_key = str(uuid.uuid4())
                all_chunks = overview.get("grounding_chunks", []) + deepdive.get(
                    "grounding_chunks", []
                )
                _judge_results = [
                    type(
                        "obj",
                        (object,),
                        {
                            "title": c.get("title", ""),
                            "link": c.get("url", ""),
                            "snippet": c.get("snippet", ""),
                        },
                    )()
                    for c in all_chunks
                ]
                fire_and_forget(
                    run_judge_evaluation(
                        run_key=_run_key,
                        query=query,
                        intent="ai_search",
                        results=_judge_results,
                        tool_name="gemini_search",
                        session_id=_resolve_session_id(ctx),
                    ),
                    name=f"judge-gemini-{_run_key[:8]}",
                )
            except Exception:
                pass

        return response
    except Exception as exc:
        duration_seconds = time.time() - start_time
        record_gemini_search(
            grounding_queries=0,
            grounding_chunks=0,
            structured_output=structured_output,
            duration_seconds=duration_seconds,
        )
        emit_tool_observability_event(
            LOGGER,
            "gemini_search",
            "error",
            level=logging.WARNING,
            query=query,
            structured_output=structured_output,
            research_goal=research_goal,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        _record_tool_failure("gemini_search")
        raise


async def grok_search(
    query: str,
    research_goal: str,
    num_results: int = 5,
    model: str | None = None,
    allowed_domains: list[str] | None = None,
    excluded_domains: list[str] | None = None,
    ctx: Context = CurrentContext(),
) -> dict:
    """Search web and X (Twitter) via Grok 4.3 via OpenRouter.

    Returns AI-synthesized answer with citations from both platforms.
    Use when you need social media data alongside web results. **Expensive tool.**

    Args:
        query: The search query string.
        research_goal: What you intend to learn. Used to focus the AI synthesis.
        num_results: Target number of web/X results to incorporate (1-10).
        model: OpenRouter model name override (default: auto-selected Grok 4.3).
        allowed_domains: Only cite results from these domains.
        excluded_domains: Exclude results from these domains.
    """
    emit_tool_observability_event(
        LOGGER,
        "grok_search",
        "request",
        query=query,
        research_goal=research_goal,
        model=model,
    )
    start_time = time.time()

    await ctx.report_progress(progress=10, total=100, message="Searching web and X via Grok 4.3...")

    try:
        result = await _grok_search_core(
            query=query,
            research_goal=research_goal,
            model=model,
            num_results=num_results,
            allowed_domains=allowed_domains,
            excluded_domains=excluded_domains,
        )

        response: dict[str, Any] = {
            "query": result.query,
            "answer": result.answer,
            "citations": result.citations,
            "model": result.model,
            "model_used": result.model_used,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "search_queries_used": result.search_queries_used,
            "error": result.error,
        }

        duration_seconds = time.time() - start_time
        emit_tool_observability_event(
            LOGGER,
            "grok_search",
            "response",
            query=query,
            answer_preview=result.answer[:200],
            citations_count=len(result.citations),
            model=result.model,
            model_used=result.model_used,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            duration_seconds=duration_seconds,
        )
        _record_tool_success(
            "grok_search",
            input_query=query,
            output_content=result.answer,
        )

        await ctx.report_progress(progress=100, total=100, message="Done")
        if settings.judge_evaluation_enabled:
            try:
                _run_key = str(uuid.uuid4())
                _judge_results = [
                    type(
                        "obj",
                        (object,),
                        {
                            "title": c.get("title", ""),
                            "link": c.get("url", ""),
                            "snippet": c.get("snippet", ""),
                        },
                    )()
                    for c in (
                        result.citations
                        if hasattr(result, "citations") and result.citations
                        else []
                    )
                ]
                fire_and_forget(
                    run_judge_evaluation(
                        run_key=_run_key,
                        query=query,
                        intent="ai_search",
                        results=_judge_results,
                        tool_name="grok_search",
                        session_id=_resolve_session_id(ctx),
                    ),
                    name=f"judge-grok-{_run_key[:8]}",
                )
            except Exception:
                pass

        return response

    except ValueError as e:
        LOGGER.warning("Grok search config error: %s", e)
        emit_tool_observability_event(
            LOGGER,
            "grok_search",
            "error",
            level=logging.WARNING,
            query=query,
            error_type=type(e).__name__,
            error_message=str(e),
        )
        _record_tool_failure("grok_search")
        return format_tool_error(e, provider="grok_openrouter")
    except httpx.HTTPError as e:
        LOGGER.warning("Grok search HTTP error: %s", e)
        emit_tool_observability_event(
            LOGGER,
            "grok_search",
            "error",
            level=logging.WARNING,
            query=query,
            error_type=type(e).__name__,
            error_message=str(e),
        )
        _record_tool_failure("grok_search")
        return format_tool_error(e, provider="grok_openrouter")
    except Exception as e:
        LOGGER.warning("Grok search unexpected error: %s", e)
        emit_tool_observability_event(
            LOGGER,
            "grok_search",
            "error",
            level=logging.WARNING,
            query=query,
            error_type=type(e).__name__,
            error_message=str(e),
        )
        _record_tool_failure("grok_search")
        return format_tool_error(e, provider="grok_openrouter")
