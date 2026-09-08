from __future__ import annotations

import logging
import time
import uuid
from typing import Annotated, Literal, cast


from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from opentelemetry import trace
from pydantic import Field

from ..errors import raise_tool_error
from ..models import ProviderWarning, WebSearchPublicResponse, WebSearchResponse
from ..search.filters import FilterValidationError, normalize_locale, resolve_window
from ..search.options import build_search_options
from ..telemetry import (
    create_chain_span,
    record_search_request,
    SEARCH_QUERY,
)
from ._helpers import (
    _record_tool_failure,
    _record_tool_success,
    _resolve_session_id,
)
from ..utils.observability import emit_tool_observability_event

LOGGER = logging.getLogger(__name__)


async def web_search(
    query: str = "",
    queries: Annotated[
        list[str] | None,
        Field(
            max_length=4,
            description="Up to 4 seed queries for multi-query rewriting; all focused on one objective. Alternative to query.",
        ),
    ] = None,
    research_goal: str = "",
    rewrite: bool = True,
    date_range: Literal["day", "week", "month", "year"] | None = None,
    after_date: str | None = None,
    before_date: str | None = None,
    language: str | None = None,
    region: str | None = None,
    gl: str | None = None,
    domain_boost: list[str] | None = None,
    reranking_instructions: Annotated[
        str | None,
        Field(
            description="Natural-language instructions steering the multi-stage reranker's ordering of results."
        ),
    ] = None,
    include_undated: Annotated[
        bool | None,
        Field(description="Set true to also include results that have no published date."),
    ] = None,
    cursor: Annotated[
        str | None,
        Field(
            description="Overflow continuation from a previous response's cursor field; pages leftover results, does not re-search."
        ),
    ] = None,
    ctx: Context = CurrentContext(),
) -> WebSearchPublicResponse:
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
    3. Ranked evidence is not page text. citation_id is the cite key; url not link.
       query_variants are dispatched branch queries. status is ok, empty, or partial.
       next is mandatory evaluate-then-fetch. cursor is optional leftover continuation
       of this run and must not be used instead of fetch. web_search(cursor=...) does
       not re-search.
    4. You MUST call fetch on the URLs in next.query.urls before treating snippets as page text.

    Args:
        query: Search query string. Be specific — include keywords, dates,
            or technical terms for better recall.
        research_goal: What you intend to learn or accomplish with this search.
            Used to validate that results serve your actual objective.
        rewrite: When True (default), LLM rewrites the query for improved
            recall and provider coverage. Set False for exact-match searches.

        domain_boost: Domains to prioritize in ranking. Boosts relevance
            scores without excluding other results.
    Temporal & locale filters:
        date_range: relative freshness bucket (day/week/month/year).
        after_date/before_date: absolute ISO YYYY-MM-DD bounds; they win over
            date_range when both are supplied.
        language: ISO 639-1 code (e.g. "en", "pl") or BCP-47 tag ("pt-BR").
        region / gl: ISO 3166-1 alpha-2 country (gl is a deprecated alias).

    Args:
        date_range: Relative freshness bucket applied across providers.
        after_date: Only results published on/after this date (YYYY-MM-DD).
        before_date: Only results published on/before this date (YYYY-MM-DD).
        language: Result language boost/filter per provider capability.
        region: Country bias/filter (alpha-2, e.g. "PL").
        gl: Deprecated alias for region.
    """
    from ..search.contracts import SearchRun, WebSearchRequest
    from ..search.service import execute_web_search
    from ..utils.http_client import get_http_client
    from ..utils.public_output import (
        decode_web_search_overflow_cursor,
        page_overflow_cursor,
        to_public_web_search_from_run,
    )

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
    if cursor and cursor.strip():
        try:
            public = page_overflow_cursor(decode_web_search_overflow_cursor(cursor.strip()))
        except ValueError as exc:
            _record_tool_failure("web_search")
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
            raise_tool_error(exc, provider="web_search")
        _record_tool_success("web_search")
        return public

    try:
        if queries:
            cleaned_queries = tuple(q.strip() for q in queries if q and q.strip())
            if len(cleaned_queries) > 4:
                raise ValueError(
                    f"queries supports up to 4 seed queries (received {len(cleaned_queries)})"
                )
            if not cleaned_queries:
                raise ValueError("queries must contain at least one non-blank string.")
            primary_query = query.strip() if (query and query.strip()) else cleaned_queries[0]
            seed_queries = cleaned_queries
        elif query and query.strip():
            primary_query = query.strip()
            seed_queries = (primary_query,)
        else:
            raise ValueError(
                f"Either query or queries must be provided and non-blank. Received: query={query!r}, queries={queries!r}. Pass a non-empty 'query' string, or 1-4 strings in 'queries'."
            )
    except Exception as exc:
        _record_tool_failure("web_search")
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
        raise_tool_error(exc, provider="web_search")

    # Resolve temporal/locale filters once; absolute bounds win over bucket.
    filter_warnings: list[str] = []
    if after_date and date_range:
        filter_warnings.append(
            "date_range ignored; absolute after_date/before_date take precedence."
        )
    try:
        temporal_window = resolve_window(
            date_range=date_range,
            after_date=after_date,
            before_date=before_date,
        )
        locale_spec = normalize_locale(language=language, region=region, gl=gl)
    except FilterValidationError as exc:
        _record_tool_failure("web_search")
        raise_tool_error(exc, provider="filters")
    except Exception as exc:
        _record_tool_failure("web_search")
        raise_tool_error(exc, provider="filters")
    if temporal_window.clamped_to_today:
        filter_warnings.append("before_date clamped to today.")
    filter_warnings.extend(locale_spec.warnings)

    search_options = build_search_options(
        locale_spec=locale_spec if (locale_spec.language or locale_spec.region) else None,
        temporal_window=temporal_window if not temporal_window.is_empty else None,
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
        include_undated=include_undated,
        domain_boost=tuple(domain_boost or []),
        pre_warnings=tuple(filter_warnings),
    )
    try:
        await ctx.report_progress(progress=5, total=100, message="Planning search...")
    except Exception:
        pass
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
                search_result = await execute_web_search(
                    request,
                    http_client=await get_http_client(),
                    run_key=tool_call_id,
                    tool_call_id=tool_call_id,
                    session_id=_resolve_session_id(ctx),
                    progress=ctx,
                    return_diagnostics=True,
                )
                response, run = cast(tuple[WebSearchResponse, SearchRun], search_result)
            except Exception as exc:
                _record_tool_failure("web_search")
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
                raise_tool_error(exc, provider="web_search")
        finally:
            reset_run_context(ctx_token)

        root_span.set_attribute("search.num_results_returned", len(response.results))
        root_span.set_status(trace.StatusCode.OK)
    record_search_request(
        providers_used=response.providers_used,
        duration_seconds=time.monotonic() - started,
        result_count=len(response.results),
    )
    emit_tool_observability_event(
        LOGGER,
        "web_search",
        "response",
        tool_call_id=tool_call_id,
        query=request.query,
        research_goal=request.research_goal,
        providers=response.providers_used,
        results=response.results,
        output_count=len(response.results),
        duration_ms=(time.monotonic() - started) * 1000,
    )
    warnings = response.warnings or []
    for warning in warnings:
        provider = warning.provider if isinstance(warning, ProviderWarning) else None
        message = warning.error if isinstance(warning, ProviderWarning) else str(warning)
        try:
            await ctx.warning(f"Provider {provider or 'unknown'}: {message}")
        except Exception:
            pass
    try:
        await ctx.report_progress(progress=100, total=100, message="Done")
    except Exception:
        pass
    _record_tool_success("web_search")
    return to_public_web_search_from_run(run)
