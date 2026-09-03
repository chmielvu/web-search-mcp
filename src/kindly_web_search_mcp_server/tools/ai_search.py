from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import httpx
from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

from ..analytics.judge_runner import run_judge_evaluation
from ..errors import raise_tool_error
from ..models import GeminiSearchResponse, GrokSearchResponse
from ..search.gemini_search_tool import gemini_search_with_grounding
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
) -> GeminiSearchResponse:
    """AI-powered search synthesis grounded with real-time Google Search results.

    When to use this tool:
    - For quick factual lookups, current event summaries, and direct AI-synthesized answers.
    - When you need inline grounding citations [N] without manually fetching multiple URLs yourself.

    Key constraints:
    - Do NOT use when you need to inspect raw source pages yourself (use web_search + fetch).
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
        result = await gemini_search_with_grounding(
            query, structured_output=structured_output, research_goal=research_goal
        )
        response = result.model_dump(exclude_none=True)
        response.pop("search_widget_html", None)
        duration_seconds = time.time() - start_time

        # Record Gemini search telemetry from overview branch
        record_gemini_search(
            grounding_queries=result.web_search_queries_count,
            grounding_chunks=result.grounding_chunks_count,
            structured_output=structured_output,
            duration_seconds=duration_seconds,
        )

        emit_tool_observability_event(
            LOGGER,
            "gemini_search",
            "response",
            query=query,
            session_id=_resolve_session_id(ctx),
            structured_output=structured_output,
            research_goal=research_goal,
            mode=result.mode,
            answer=result.answer,
            structured_data=result.structured_data,
            sources=result.sources,
            url_citations=result.url_citations,
            search_queries=result.search_queries,
            model=result.model_used,
            model_used=result.model_used,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            grounding_chunks_count=result.grounding_chunks_count,
            web_search_queries_count=result.web_search_queries_count,
            fallback_chain=result.fallback_chain,
            fallback_reason=result.fallback_reason,
            output_count=len(result.sources) + len(result.url_citations),
            duration_ms=duration_seconds * 1000,
            error=result.error,
        )
        _record_tool_success(
            "gemini_search",
            input_query=query,
            output_content=result.answer,
        )
        await ctx.report_progress(progress=100, total=100, message="Done")
        if settings.judge_evaluation_enabled:
            try:
                _run_key = str(uuid.uuid4())
                all_chunks = result.sources
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
        raise_tool_error(exc, provider="gemini")


async def grok_search(
    query: str,
    research_goal: str,
    num_results: int = 5,
    model: str | None = None,
    allowed_domains: list[str] | None = None,
    excluded_domains: list[str] | None = None,
    ctx: Context = CurrentContext(),
) -> GrokSearchResponse:
    """Search the web and public X posts with native xAI Grok tools.

    Returns an AI-synthesized answer with citations from web and X. This uses
    the direct xAI Responses API because Vertex's managed Grok Responses
    endpoint does not currently expose xAI's native search tools. **Expensive
    tool**: xAI bills server-side web/X tool invocations separately.

    Args:
        query: The search query string.
        research_goal: What you intend to learn. Used to focus the AI synthesis.
        num_results: Target number of web/X results to incorporate (1-10).
        model: Direct xAI model override (default: configured Grok model).
        allowed_domains: Only cite results from these web domains (max 5).
        excluded_domains: Exclude results from these web domains (max 5).
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

    await ctx.report_progress(
        progress=10,
        total=100,
        message="Searching web and X via native xAI Grok tools...",
    )

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
            "backend": result.backend,
            "web_search_calls": result.web_search_calls,
            "x_search_calls": result.x_search_calls,
            "sources_used": result.sources_used,
            "cached_input_tokens": result.cached_input_tokens,
            "reasoning_tokens": result.reasoning_tokens,
            "total_tokens": result.total_tokens,
            "error": result.error,
            "usage": {
                "prompt_tokens": result.input_tokens,
                "completion_tokens": result.output_tokens,
                "total_tokens": result.total_tokens,
                "cached_input_tokens": result.cached_input_tokens,
                "reasoning_tokens": result.reasoning_tokens,
                "model_used": result.model_used or result.model,
                "provider": "grok",
            },
        }

        duration_seconds = time.time() - start_time
        emit_tool_observability_event(
            LOGGER,
            "grok_search",
            "response",
            query=query,
            research_goal=research_goal,
            answer=result.answer,
            answer_preview=result.answer[:200],
            citations=result.citations,
            output_count=len(result.citations),
            citations_count=len(result.citations),
            model=result.model,
            model_used=result.model_used,
            backend=result.backend,
            web_search_calls=result.web_search_calls,
            x_search_calls=result.x_search_calls,
            sources_used=result.sources_used,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cached_input_tokens=result.cached_input_tokens,
            reasoning_tokens=result.reasoning_tokens,
            total_tokens=result.total_tokens,
            duration_ms=duration_seconds * 1000,
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
        raise_tool_error(e, provider="grok_xai")
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
        raise_tool_error(e, provider="grok_xai")
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
        raise_tool_error(e, provider="grok_xai")
