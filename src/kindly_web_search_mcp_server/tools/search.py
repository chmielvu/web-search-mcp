from __future__ import annotations

import logging
import time
import uuid

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from opentelemetry import trace

from ..models import WebSearchResultType
from ..search.options import build_search_options
from ..telemetry import (
    create_chain_span,
    record_search_request,
    SEARCH_NUM_RESULTS_REQUESTED,
    SEARCH_QUERY,
)
from ._helpers import (
    _apply_domain_filters,
    _normalize_lightweight_search_response,
    _resolve_session_id,
)

LOGGER = logging.getLogger(__name__)


async def web_search(
    query: str,
    research_goal: str,
    rewrite: bool = True,
    num_results: int = 15,
    result_offset: int = 0,
    site_filters: list[str] | None = None,
    domain_filters: list[str] | None = None,
    domain_boost: list[str] | None = None,
    domain_block: list[str] | None = None,
    ctx: Context = CurrentContext(),
) -> WebSearchResultType:
    """Run one validated multi-provider web search.

    ``research_goal`` is mandatory. ``num_results`` must be between 15 and 50.
    Set ``rewrite=False`` to skip only LLM rewriting; deterministic enrichment
    and provider target coverage still run.
    """
    from ..search.contracts import WebSearchRequest
    from ..search.service import execute_web_search
    from ..utils.http_client import get_http_client

    started = time.monotonic()
    tool_call_id = str(uuid.uuid4())
    search_options = build_search_options(
        result_offset=result_offset,
        site_filters=[*(site_filters or []), *(domain_filters or [])],
    )
    request = WebSearchRequest(
        query=query,
        research_goal=research_goal,
        rewrite=rewrite,
        num_results=num_results,
        options=search_options,
    )
    await ctx.report_progress(progress=5, total=100, message="Planning search...")
    with create_chain_span(
        "web_search",
        attributes={
            SEARCH_QUERY: request.query[:500],
            SEARCH_NUM_RESULTS_REQUESTED: request.num_results,
            "search.rewrite_enabled": request.rewrite,
            "search.research_goal": request.research_goal[:500],
            "search.result_offset": request.options.result_offset,
        },
    ) as root_span:
        response_model = await execute_web_search(
            request,
            http_client=await get_http_client(),
            run_key=tool_call_id,
            tool_call_id=tool_call_id,
            session_id=_resolve_session_id(ctx),
            progress=ctx,
        )
        response = _normalize_lightweight_search_response(
            response_model.model_dump(exclude_none=True), query=request.query
        )
        if domain_boost or domain_block:
            response["results"] = _apply_domain_filters(
                response.get("results", []), domain_boost, domain_block
            )
        root_span.set_attribute("search.num_results_returned", len(response.get("results", [])))
        root_span.set_status(trace.StatusCode.OK)
    record_search_request(
        providers_used=response.get("providers_used", []),
        duration_seconds=time.monotonic() - started,
        result_count=len(response.get("results", [])),
    )
    await ctx.report_progress(progress=100, total=100, message="Done")
    return response  # type: ignore[return-value]
