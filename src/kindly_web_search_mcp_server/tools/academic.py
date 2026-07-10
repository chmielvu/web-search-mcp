from __future__ import annotations

import json
import logging
from typing import Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

from ..cache import get_query_cache, provider_cache_key
from ..models import AcademicSearchResultType
from ..search.normalize import normalize_query
from ..utils.observability import emit_tool_observability_event
from ._helpers import _academic_search_flight, _record_tool_failure, _record_tool_success

LOGGER = logging.getLogger(__name__)


async def academic_search(
    query: str,
    limit: int = 5,
    sources: list[str] | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    fields_of_study: list[str] | None = None,
    venue: str | None = None,
    open_access_only: bool = False,
    sort: str = "relevance",
    ctx: Context = CurrentContext(),
) -> AcademicSearchResultType:
    """Search 6 scholarly sources (Semantic Scholar, arXiv, OpenAlex, CrossRef, PubMed, CORE) with cross-source deduplication.
    Supports year, venue, field-of-study, and open-access filters.
    """
    limit = max(1, min(limit, 20))
    if sort not in ("relevance", "citations", "date"):
        sort = "relevance"

    await ctx.report_progress(progress=5, total=100, message="Checking cache...")
    await ctx.info(f"Academic search: {query[:80]}...")

    normalized_query = normalize_query(query)
    sources_key = provider_cache_key(sources)

    emit_tool_observability_event(
        LOGGER,
        "academic_search",
        "request",
        query=query,
        normalized_query=normalized_query,
        limit=limit,
        sources=sources,
        sources_key=sources_key,
        year_from=year_from,
        year_to=year_to,
        fields_of_study=fields_of_study,
        venue=venue,
        open_access_only=open_access_only,
        sort=sort,
    )

    filter_params: dict[str, Any] = {
        "year_from": year_from,
        "year_to": year_to,
        "fields_of_study": sorted(fields_of_study) if fields_of_study else None,
        "venue": venue,
        "open_access_only": open_access_only,
        "sort": sort,
    }

    filter_key = json.dumps(filter_params, sort_keys=True, default=str)
    cache_providers_key = f"academic:{sources_key}:{filter_key[:24]}"

    try:
        exact_cache = get_query_cache()
        exact_cached = exact_cache.lookup(
            normalized_query=normalized_query,
            num_results=limit,
            rewrite_enabled=True,
            search_mode="academic",
            providers_key=cache_providers_key,
        )
        if exact_cached:
            LOGGER.debug("Exact query cache hit for academic search: %s", query[:100])
            # Copy before mutating so concurrent requests don't corrupt the
            # shared cached object.
            exact_cached = dict(exact_cached)
            exact_cached["query"] = query
            emit_tool_observability_event(
                LOGGER,
                "academic_search",
                "response",
                cache_hit="exact",
                query=query,
                result_count=len(exact_cached.get("results", [])),
                sources_used=exact_cached.get("sources_used", []),
            )
            _record_tool_success(
                "academic_search",
                input_query=query,
                output_result_count=len(exact_cached.get("results", [])),
            )
            return exact_cached  # type: ignore[return-value]
    except Exception as e:
        LOGGER.warning("Exact query cache lookup failed for academic search: %s", e)

    await ctx.report_progress(progress=20, total=100, message="Searching academic sources...")

    async def _execute_academic_search() -> dict:
        from ..search.academic_search_orchestrator import run_academic_search

        result = await run_academic_search(
            query,
            limit=limit,
            sources=sources,
            year_from=year_from,
            year_to=year_to,
            fields_of_study=fields_of_study,
            venue=venue,
            open_access_only=open_access_only,
            sort=sort,
        )
        response = result.model_dump(exclude_none=True)

        try:
            exact_cache = get_query_cache()
            exact_cache.store(
                normalized_query=normalized_query,
                num_results=limit,
                rewrite_enabled=True,
                response=response,
                search_mode="academic",
                providers_key=cache_providers_key,
            )
            LOGGER.debug("Stored exact query cache for academic search: %s", query[:100])
        except Exception as e:
            LOGGER.warning("Exact query cache write failed for academic search: %s", e)

        return response

    try:
        flight_key = _academic_search_flight.make_key(
            normalized_query, limit, sources_key, filter_key
        )
        response = await _academic_search_flight.do(flight_key, _execute_academic_search)

        _record_tool_success(
            "academic_search",
            input_query=query,
            output_result_count=len(response.get("results", [])),
        )
        emit_tool_observability_event(
            LOGGER,
            "academic_search",
            "response",
            cache_hit="miss",
            query=query,
            normalized_query=normalized_query,
            result_count=len(response.get("results", [])),
            sources_used=response.get("sources_used", []),
            warnings=response.get("warnings", []),
            results=response.get("results", []),
        )
        await ctx.report_progress(progress=100, total=100, message="Done")
        await ctx.info(
            f"Found {len(response.get('results', []))} academic results from {response.get('sources_used', [])}"
        )
        return response
    except Exception as e:
        LOGGER.warning("Academic search failed: %s", e)
        _record_tool_failure("academic_search")
        emit_tool_observability_event(
            LOGGER,
            "academic_search",
            "error",
            level=30,
            query=query,
            error_type=type(e).__name__,
            error_message=str(e)[:200],
        )
        from ..errors import format_tool_error

        return format_tool_error(e, provider="academic_search")  # type: ignore[return-value]
