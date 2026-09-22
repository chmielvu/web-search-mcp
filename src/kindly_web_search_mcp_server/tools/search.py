from __future__ import annotations

import contextlib
import logging
import time
import uuid
from typing import Annotated, Literal, cast

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from opentelemetry import trace
from pydantic import Field

from ..analytics.producers import emit_tool_observability_event
from ..errors import raise_tool_error
from ..models import ProviderWarning, WebSearchPublicResponse
from ..search.filters import FilterValidationError, normalize_locale, resolve_window
from ..search.options import SearchOptions
from ..search.types import SearchRunResult
from ..telemetry import (
    SEARCH_QUERY,
    create_chain_span,
    record_search_request,
)
from ..utils.input_optimization import strip_site_operators
from ._helpers import (
    _record_tool_failure,
    _record_tool_success,
    _resolve_session_id,
)

LOGGER = logging.getLogger(__name__)


async def web_search(
    research_goal: Annotated[
        str,
        Field(description=("Required search objective describing what you need the results for.")),
    ],
    query: Annotated[
        str,
        Field(
            description=(
                'Search string, e.g. "Python 3.14 free-threading PEP 779". Provide this or queries.'
            )
        ),
    ] = "",
    queries: Annotated[
        list[str] | None,
        Field(
            max_length=4,
            description=(
                "Up to 4 seed strings for one objective, e.g. "
                '["Python 3.14 free-threading status", "PEP 779 supported"]. '
                "Takes precedence over query when both are set."
            ),
        ),
    ] = None,
    rewrite: Annotated[
        bool,
        Field(
            description=(
                "Rewrite the query for broader recall in the first wave (default true). "
                "Set false for exact literals, error strings, or quoted identifiers. "
                "Does not disable adaptive search; follow-up waves always run."
            )
        ),
    ] = True,
    date_range: Annotated[
        Literal["day", "week", "month", "year"] | None,
        Field(description="Relative freshness: day, week, month, or year."),
    ] = None,
    after_date: Annotated[
        str | None,
        Field(
            description=(
                "Keep pages published on/after this date, format YYYY-MM-DD "
                '(e.g. "2026-01-01"). Overrides date_range when both are set.'
            )
        ),
    ] = None,
    before_date: Annotated[
        str | None,
        Field(
            description=(
                "Keep pages published on/before this date, format YYYY-MM-DD "
                '(e.g. "2026-09-17"). Overrides date_range when both are set.'
            )
        ),
    ] = None,
    language: Annotated[
        str | None,
        Field(
            description='Language boost where the provider supports it. ISO 639-1 or BCP-47, e.g. "en" or "pt-BR".'
        ),
    ] = None,
    region: Annotated[
        str | None,
        Field(
            description='Country bias where the provider supports it. ISO 3166-1 alpha-2, e.g. "PL".'
        ),
    ] = None,
    domain_boost: Annotated[
        list[str] | None,
        Field(
            description=(
                "Domains to rank higher without excluding others, e.g. "
                '["docs.python.org", "peps.python.org"].'
            )
        ),
    ] = None,
    reranking_instructions: Annotated[
        str | None,
        Field(
            description=(
                "Natural-language ordering preference for the reranker, e.g. "
                '"Prefer official docs and PEPs over blogs."'
            )
        ),
    ] = None,
    include_undated: Annotated[
        bool | None,
        Field(
            description=(
                "Undated pages under a date window: omit for the default "
                "(drop only from providers without native dates), true to keep them, "
                "false to drop all undated pages."
            )
        ),
    ] = None,
    cursor: Annotated[
        str | None,
        Field(
            description=(
                "Opaque page token from response.cursor. Pages leftover links "
                "from that run; does not re-search. Cursor pages return title "
                "and url only; pass response.run_key alongside cursor."
            )
        ),
    ] = None,
    run_key: Annotated[
        str | None,
        Field(
            description=(
                "response.run_key from the search that produced the cursor. "
                "Required together with cursor; identifies the retained run "
                "whose leftover links to page."
            )
        ),
    ] = None,
    ctx: Context = CurrentContext(),
) -> WebSearchPublicResponse:
    """Ranked web results from multiple engines, with bounded adaptive retrieval.

    A fresh search runs the broad first fanout unchanged, then an LLM picks 1-2
    targeted follow-up queries, then another LLM decides between finishing and
    one final targeted wave. The response carries globally ranked `results`
    (citation_id, title, url, snippet, domain, published_date, freshness,
    consensus, providers), a snippet-grounded `synthesis` with inline [cN]
    citations, and `search` execution metadata (rounds 1-3, stop_reason).

    Snippets are teasers — call fetch on at most 5 URLs. Empty `results` means
    no hits; read `warnings` for why. `warnings` with results means some
    providers or filters degraded; keep the ranked hits. `cursor` pages leftover
    links from this run (title/url only, no synthesis, no new searching). `next`
    is a fetch call of the top URLs (max 5).

    Use when you need source URLs, publication dates, or agreement across engines.
    Do not use for a one-shot factual answer (gemini_search), first-pass
    reconnaissance (quick_web_search), library docs (quick_web_search mode="docs"),
    or full page text (fetch).

    Provide `query` or `queries`. `queries` wins when both are set. Omit `cursor`
    for a new search.

    Errors: invalid cursor → search again without cursor; invalid dates → use
    YYYY-MM-DD; missing query → supply query or queries.
    """
    from ..search.contracts import SearchRun, WebSearchRequest
    from ..search.service import execute_web_search
    from ..utils.http_client import get_http_client
    from ..utils.public_output import page_overflow_cursor, to_public_web_search_from_run

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
            if not (run_key and run_key.strip()):
                raise ValueError("run_key is required together with cursor.")
            public = page_overflow_cursor(run_key.strip(), cursor.strip())
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
        if not (research_goal and research_goal.strip()):
            raise ValueError("research_goal must be provided and non-blank.")
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

    # Strip legacy search-engine operators (site:, after:, before:, filetype:)
    # from all seed queries before they reach providers.  Domain/date filters
    # should use API parameters (domain_boost, after_date, etc.) instead.
    stripped_ops: list[str] = []
    cleaned_seeds: list[str] = []
    for sq in seed_queries:
        cleaned, ops = strip_site_operators(sq)
        stripped_ops.extend(ops)
        if cleaned:
            cleaned_seeds.append(cleaned)
    if stripped_ops:
        LOGGER.debug("Stripped operators from web_search queries: %s", stripped_ops)
    if cleaned_seeds:
        seed_queries = tuple(cleaned_seeds)
        primary_query = cleaned_seeds[0]

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
        locale_spec = normalize_locale(language=language, region=region, gl=None)
    except FilterValidationError as exc:
        _record_tool_failure("web_search")
        raise_tool_error(exc, provider="filters")
    except Exception as exc:
        _record_tool_failure("web_search")
        raise_tool_error(exc, provider="filters")
    if temporal_window.clamped_to_today:
        filter_warnings.append("before_date clamped to today.")
    filter_warnings.extend(locale_spec.warnings)
    search_options = SearchOptions(
        temporal=temporal_window if not temporal_window.is_empty else None,
        language=(locale_spec.language if locale_spec else None),
        region=(locale_spec.region if locale_spec else None),
    ).validate()
    effective_research_goal = research_goal.strip()
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
    with contextlib.suppress(Exception):
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
                search_result = await execute_web_search(
                    request,
                    http_client=await get_http_client(),
                    run_key=tool_call_id,
                    tool_call_id=tool_call_id,
                    session_id=_resolve_session_id(ctx),
                    progress=ctx,
                    return_diagnostics=True,
                )
                response, run = cast(tuple[SearchRunResult, SearchRun], search_result)
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

        root_span.set_attribute("search.num_results_returned", len(response.hits))
        root_span.set_status(trace.StatusCode.OK)
    record_search_request(
        providers_used=list(response.providers_used),
        duration_seconds=time.monotonic() - started,
        result_count=len(response.hits),
    )
    emit_tool_observability_event(
        LOGGER,
        "web_search",
        "response",
        tool_call_id=tool_call_id,
        query=request.query,
        research_goal=request.research_goal,
        providers=response.providers_used,
        results=response.hits,
        output_count=len(response.hits),
        duration_ms=(time.monotonic() - started) * 1000,
    )
    warnings = response.warnings or []
    for warning in warnings:
        provider = warning.provider if isinstance(warning, ProviderWarning) else None
        message = warning.error if isinstance(warning, ProviderWarning) else str(warning)
        with contextlib.suppress(Exception):
            await ctx.warning(f"Provider {provider or 'unknown'}: {message}")
    with contextlib.suppress(Exception):
        await ctx.report_progress(progress=100, total=100, message="Done")
    _record_tool_success("web_search")
    return to_public_web_search_from_run(run)
