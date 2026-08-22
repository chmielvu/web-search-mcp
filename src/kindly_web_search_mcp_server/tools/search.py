from __future__ import annotations

import logging
import time
import uuid

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from opentelemetry import trace

from ..models import WebSearchResponse
from ..search.options import build_search_options
from ..telemetry import (
    create_chain_span,
    record_search_request,
    SEARCH_QUERY,
)
from ._helpers import (
    _apply_domain_filters,
    _normalize_lightweight_search_response,
    _resolve_session_id,
)
from ..utils.observability import emit_tool_observability_event

LOGGER = logging.getLogger(__name__)


async def web_search(
    query: str = "",
    queries: list[str] | None = None,
    research_goal: str = "",
    rewrite: bool = True,
    site_filters: list[str] | None = None,
    domain_filters: list[str] | None = None,
    domain_boost: list[str] | None = None,
    domain_block: list[str] | None = None,
    reranking_instructions: str | None = None,
    ctx: Context = CurrentContext(),
) -> WebSearchResponse:
    """Run one validated multi-provider web search across configured backends with RRF ranking.

    Multi-query input:
    - You may pass up to 4 input seed queries via `queries` (e.g. `queries=["query 1", "query 2"]`).
    - Keep all input queries focused on a single topic/search objective to ensure coherent search planning.

    When to use this tool:
    - For thorough, deep multi-provider search across web engines (Brave, Tavily, SearXNG, etc.).
    - When you need domain filtering, intent classification, and provider consensus signals.

    Selection & Chaining Process:
    1. Provide a specific search query containing exact terms, error codes, or dates.
    2. Provide a natural-language research_goal used for intent policy and relevance scoring.
    3. Evaluate results based on provider_count (>=2 indicates high consensus).
    4. You MUST call fetch on the top URLs to read their full context before finalizing your answer.

    Do NOT use for:
    - Initial fast scoping (use quick_web_search first).
    - AI-grounded synthesized answers (use gemini_search instead).

    Args:
        query: Search query string. Be specific — include keywords, dates,
            or technical terms for better recall.
        research_goal: What you intend to learn or accomplish with this search.
            Used to validate that results serve your actual objective.
        rewrite: When True (default), LLM rewrites the query for improved
            recall and provider coverage. Set False for exact-match searches.
        site_filters: Restrict results to these domains (e.g., ["arxiv.org",
            "github.com"]). Applied after provider-level filtering.
        domain_filters: Blocklist patterns to exclude results (e.g.,
            ["*pinterest.*"]). Supports wildcards.
        domain_boost: Domains to prioritize in ranking. Boosts relevance
            scores without excluding other results.
        domain_block: Domains to exclude entirely from results.
    """
    from ..search.contracts import WebSearchRequest
    from ..search.service import execute_web_search
    from ..utils.http_client import get_http_client

    started = time.monotonic()
    tool_call_id = str(uuid.uuid4())
    emit_tool_observability_event(
        LOGGER,
        "web_search",
        "request",
        tool_call_id=tool_call_id,
        query=query,
        queries=queries,
        research_goal=research_goal,
        rewrite=rewrite,
    )
    try:
        if queries:
            cleaned_queries = tuple(q.strip() for q in queries if q and q.strip())[:4]
            if not cleaned_queries:
                raise ValueError("queries must contain at least one non-blank string.")
            primary_query = query.strip() if (query and query.strip()) else cleaned_queries[0]
            seed_queries = cleaned_queries
        elif query and query.strip():
            primary_query = query.strip()
            seed_queries = (primary_query,)
        else:
            raise ValueError("Either query or queries must be provided and non-blank.")
    except Exception as exc:
        emit_tool_observability_event(
            LOGGER,
            "web_search",
            "error",
            tool_call_id=tool_call_id,
            query=query,
            research_goal=research_goal,
            error_type=type(exc).__name__,
            error_message=str(exc),
            duration_ms=(time.monotonic() - started) * 1000,
        )
        raise

    search_options = build_search_options(
        site_filters=site_filters or None,
        domain_filters=domain_filters or None,
    )
    effective_research_goal = (
        research_goal.strip() if (research_goal and research_goal.strip()) else primary_query
    )
    request = WebSearchRequest(
        query=primary_query,
        queries=seed_queries,
        research_goal=effective_research_goal,
        rewrite=rewrite,
        options=search_options,
        reranking_instructions=reranking_instructions,
    )
    await ctx.report_progress(progress=5, total=100, message="Planning search...")
    with create_chain_span(
        "web_search",
        attributes={
            SEARCH_QUERY: request.query[:500],
            "search.rewrite_enabled": request.rewrite,
            "search.research_goal": request.research_goal[:500],
        },
    ) as root_span:
        from ..inference.engine import bind_run_context, reset_run_context

        ctx_token = bind_run_context(tool_call_id, operation="web_search")
        try:
            try:
                response_model = await execute_web_search(
                    request,
                    http_client=await get_http_client(),
                    run_key=tool_call_id,
                    tool_call_id=tool_call_id,
                    session_id=_resolve_session_id(ctx),
                    progress=ctx,
                )
            except Exception as exc:
                emit_tool_observability_event(
                    LOGGER,
                    "web_search",
                    "error",
                    tool_call_id=tool_call_id,
                    query=request.query,
                    research_goal=request.research_goal,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    duration_ms=(time.monotonic() - started) * 1000,
                )
                raise
            if isinstance(response_model, tuple):
                response_model = response_model[0]
            response = _normalize_lightweight_search_response(
                response_model.model_dump(exclude_none=True),
                query=request.query,
            )
        finally:
            reset_run_context(ctx_token)

        if domain_boost or domain_block or domain_filters or site_filters:
            combined_block = (
                [*domain_block, *(domain_filters or [])] if domain_block else domain_filters
            )
            response["results"] = _apply_domain_filters(
                response.get("results", []),
                domain_boost,
                combined_block,
                site_filters=site_filters or None,
            )
        root_span.set_attribute("search.num_results_returned", len(response.get("results", [])))
        root_span.set_status(trace.StatusCode.OK)
    record_search_request(
        providers_used=response.get("providers_used", []),
        duration_seconds=time.monotonic() - started,
        result_count=len(response.get("results", [])),
    )
    emit_tool_observability_event(
        LOGGER,
        "web_search",
        "response",
        tool_call_id=tool_call_id,
        query=request.query,
        research_goal=request.research_goal,
        providers=response.get("providers_used", []),
        results=response.get("results", []),
        output_count=len(response.get("results", [])),
        duration_ms=(time.monotonic() - started) * 1000,
    )
    warnings = response.get("warnings") or []
    for warning in warnings:
        provider = warning.get("provider") if isinstance(warning, dict) else None
        message = warning.get("message") if isinstance(warning, dict) else str(warning)
        await ctx.warning(f"Provider {provider or 'unknown'}: {message}")
    await ctx.report_progress(progress=100, total=100, message="Done")
    return response  # type: ignore[return-value]
