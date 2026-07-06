from __future__ import annotations

import logging
import os
import time
import uuid

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from opentelemetry import trace

from ..cache import get_query_cache
from ..models import WebSearchResultType
from ..search.normalize import normalize_query
from ..search.options import build_search_identity_key, build_search_options
from ..search.pipeline import run_search_pipeline as run_web_search
from ..telemetry import (
    create_chain_span,
    record_search_request,
    SEARCH_NUM_RESULTS_REQUESTED,
    SEARCH_QUERY,
)
from ..utils.diagnostics import Diagnostics, diagnostics_enabled, mask_env_values, new_request_id
from ..utils.observability import emit_tool_observability_event
from ._helpers import (
    _apply_domain_filters,
    _normalize_lightweight_search_response,
    _record_tool_success,
    _record_web_search_return,
    _resolve_session_id,
    _search_flight,
)

LOGGER = logging.getLogger(__name__)


async def web_search(
    query: str,
    research_goal: str,
    rewrite: bool = True,
    num_results: int = 15,
    result_offset: int = 0,
    searxng_categories: list[str] | None = None,
    searxng_engines: list[str] | None = None,
    searxng_language: str | None = None,
    searxng_pageno: int = 1,
    searxng_time_range: str | None = None,
    searxng_safesearch: int | None = None,
    site_filters: list[str] | None = None,
    domain_filters: list[str] | None = None,
    domain_boost: list[str] | None = None,
    domain_block: list[str] | None = None,
    ctx: Context = CurrentContext(),
) -> WebSearchResultType:
    """Multi-provider web search returning lightweight results (title, link, snippet, provider_count).
    Default discovery tool. Set rewrite=False only for exact-literals: errors, URLs, versions, hashes.
    Uses provider_count as an agreement signal across multiple sources.

    When to use:
    - Initial research and discovery before deeper content extraction
    - Fact-checking with multi-source verification
    - Finding authoritative sources on any topic

    When not to use:
    - Use get_content to extract full page content from a known URL
    - Use gemini_search for AI-synthesized answers

    Configuration requires SEARXNG_BASE_URL and TAVILY_API_KEY environment variables.
    num_results default is 10; recommended range is 15-50. rewrite=True enables normal discovery (best for most queries). rewrite=False is for exact lookups.
    """

    start_time = time.time()
    tool_call_id = str(uuid.uuid4())

    # Enforce bounds
    num_results = max(15, min(num_results, 50))
    search_options = build_search_options(
        result_offset=result_offset,
        searxng_categories=searxng_categories,
        searxng_engines=searxng_engines,
        searxng_language=searxng_language,
        searxng_pageno=searxng_pageno,
        searxng_time_range=searxng_time_range,
        searxng_safesearch=searxng_safesearch,
        site_filters=site_filters,
        domain_filters=domain_filters,
    )
    search_identity_key = build_search_identity_key(None, search_options)

    # Create root span for entire web_search operation
    with create_chain_span(
        "mcp.tool.web_search",
        attributes={
            SEARCH_QUERY: query[:500],
            SEARCH_NUM_RESULTS_REQUESTED: num_results,
            "search.rewrite_enabled": str(rewrite).lower(),
            "search.research_goal": research_goal[:500],
            "search.result_offset": result_offset,
            "search.identity_key": search_identity_key,
        },
    ) as root_span:
        # Report progress: starting
        await ctx.report_progress(progress=5, total=100, message="Checking cache...")
        await ctx.info(f"Searching: {query[:80]}...")

        # 1. Exact query cache lookup (fastest, deterministic)
        normalized_query = normalize_query(query)

        emit_tool_observability_event(
            LOGGER,
            "web_search",
            "request",
            query=query,
            normalized_query=normalized_query,
            research_goal=research_goal,
            num_results=num_results,
            result_offset=result_offset,
            rewrite_enabled=rewrite,
            providers_key=search_identity_key,
            search_options=search_options.to_dict(),
        )
        try:
            exact_cache = get_query_cache()
            exact_cached = exact_cache.lookup(
                normalized_query=normalized_query,
                num_results=num_results,
                rewrite_enabled=rewrite,
                search_mode="balanced",  # Current default mode
                providers_key=search_identity_key,
            )
            if exact_cached:
                LOGGER.debug("Exact query cache hit for: %s", query[:100])
                root_span.set_attribute("cache.hit", "exact")
                root_span.set_attribute(
                    "search.num_results_returned", len(exact_cached.get("results", []))
                )
                exact_response = _normalize_lightweight_search_response(exact_cached, query=query)
                emit_tool_observability_event(
                    LOGGER,
                    "web_search",
                    "response",
                    cache_hit="exact",
                    query=query,
                    normalized_query=normalized_query,
                    research_goal=research_goal,
                    result_count=len(exact_response.get("results", [])),
                    providers_used=exact_response.get("providers_used", []),
                    warnings=exact_response.get("warnings", []),
                    results=exact_response.get("results", []),
                    result_window=exact_response.get("result_window"),
                )
                _record_web_search_return(
                    tool_call_id=tool_call_id,
                    cache_hit="exact",
                    query=query,
                    normalized_query=normalized_query,
                    research_goal=research_goal,
                    rewrite_enabled=rewrite,
                    result_offset=result_offset,
                    num_results_requested=num_results,
                    search_identity_key=search_identity_key,
                    search_options=search_options,
                    response=exact_response,
                    domain_boost=domain_boost,
                    domain_block=domain_block,
                )
                _record_tool_success(
                    "web_search",
                    input_query=query,
                    output_result_count=len(exact_response.get("results", [])),
                )
                record_search_request(
                    providers_used=exact_response.get("providers_used", []),
                    duration_seconds=time.time() - start_time,
                    result_count=len(exact_response.get("results", [])),
                )
                return exact_response  # type: ignore[return-value]
        except Exception as e:
            LOGGER.warning("Exact query cache lookup failed: %s", e)

        root_span.set_attribute("cache.hit", "miss")

        # Report progress: rewriting and searching
        if rewrite:
            await ctx.report_progress(progress=20, total=100, message="Rewriting query...")
        else:
            await ctx.report_progress(progress=20, total=100, message="Querying providers...")

        diag_enabled = diagnostics_enabled()

        # Report progress: executing search
        await ctx.report_progress(progress=35, total=100, message="Querying providers...")

        # SingleFlight: coalesce identical concurrent searches into one execution
        flight_key = _search_flight.make_key(
            normalized_query, num_results, rewrite, search_identity_key
        )

        async def _execute_search() -> dict:
            import sys

            parent_request_id = new_request_id() if diag_enabled else ""
            parent_diag = Diagnostics(parent_request_id, diag_enabled, stream=sys.stderr)
            if diag_enabled:
                env_snapshot = {
                    "SEARXNG_BASE_URL": os.environ.get("SEARXNG_BASE_URL", ""),
                    "TAVILY_API_KEY": os.environ.get("TAVILY_API_KEY", ""),
                    "BRAVE_API_KEY": os.environ.get("BRAVE_API_KEY", ""),
                    "JINA_API_KEY": os.environ.get("JINA_API_KEY", ""),
                    "COMPOSIO_API_KEY": os.environ.get("COMPOSIO_API_KEY", ""),
                    "SEARCH_ROUTER_API_KEY": os.environ.get("SEARCH_ROUTER_API_KEY", ""),
                    "GITHUB_TOKEN": os.environ.get("GITHUB_TOKEN", ""),
                    "TOOL_TOTAL_TIMEOUT_SECONDS": os.environ.get("TOOL_TOTAL_TIMEOUT_SECONDS", ""),
                    "TOOL_TOTAL_TIMEOUT_MAX_SECONDS": os.environ.get(
                        "TOOL_TOTAL_TIMEOUT_MAX_SECONDS", ""
                    ),
                    "WEB_SEARCH_MAX_CONCURRENCY": os.environ.get("WEB_SEARCH_MAX_CONCURRENCY", ""),
                }
                parent_diag.emit(
                    "web_search.start",
                    "Starting web search",
                    {
                        "query": query,
                        "num_results": num_results,
                        "result_offset": result_offset,
                        "search_options": search_options.to_dict(),
                        "env": mask_env_values(env_snapshot),
                    },
                )
            response_model = await run_web_search(
                query,
                num_results=num_results,
                rewrite=rewrite,
                diagnostics=parent_diag,
                research_goal=research_goal,
                search_options=search_options,
                session_id=_resolve_session_id(ctx),
                tool_call_id=tool_call_id,
            )
            _response = _normalize_lightweight_search_response(
                response_model.model_dump(exclude_none=True),
                query=query,
            )
            if not response_model.results:
                return _response

            # Cache write: exact query cache
            try:
                exact_cache = get_query_cache()
                exact_cache.store(
                    normalized_query=normalized_query,
                    num_results=num_results,
                    rewrite_enabled=rewrite,
                    response=_response,
                    search_mode="balanced",
                    providers_key=search_identity_key,
                )
                LOGGER.debug("Stored exact query cache for: %s", query[:100])
            except Exception as e:
                LOGGER.warning("Exact query cache write failed: %s", e)

            return _response

        response = await _search_flight.do(flight_key, _execute_search)

        if domain_boost or domain_block:
            response["results"] = _apply_domain_filters(
                response.get("results", []), domain_boost, domain_block
            )

        _record_web_search_return(
            tool_call_id=tool_call_id,
            cache_hit="miss",
            query=query,
            normalized_query=normalized_query,
            research_goal=research_goal,
            rewrite_enabled=rewrite,
            result_offset=result_offset,
            num_results_requested=num_results,
            search_identity_key=search_identity_key,
            search_options=search_options,
            response=response,
            domain_boost=domain_boost,
            domain_block=domain_block,
        )

        # Add final span attributes
        root_span.set_attribute("search.num_results_returned", len(response.get("results", [])))
        root_span.set_status(trace.StatusCode.OK)
        emit_tool_observability_event(
            LOGGER,
            "web_search",
            "response",
            cache_hit="miss",
            query=query,
            normalized_query=normalized_query,
            research_goal=research_goal,
            result_count=len(response.get("results", [])),
            providers_used=response.get("providers_used", []),
            warnings=response.get("warnings", []),
            results=response.get("results", []),
            result_window=response.get("result_window"),
        )
        _record_tool_success(
            "web_search",
            input_query=query,
            output_result_count=len(response.get("results", [])),
        )

        # Report completion
        await ctx.report_progress(progress=100, total=100, message="Done")
        await ctx.info(f"Found {len(response.get('results', []))} results")

        return response
