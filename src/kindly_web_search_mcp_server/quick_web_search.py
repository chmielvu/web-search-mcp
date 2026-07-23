"""Standalone quick_web_search MCP tool backed by Parallel AI Search API.

Locks mode to ``advanced`` and exposes the full Parallel Search surface:
required ``search_queries`` + ``objective``, plus domain/location filters,
freshness, fetch policy, and excerpt sizing.  Returns rich metadata
(search_id, session_id, warnings, usage) alongside citations with
publish dates and raw excerpts.
"""

from __future__ import annotations

from typing import Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import BaseModel, Field
from parallel import AsyncParallel

from .errors import format_tool_error
from .settings import settings
from .tools.catalog import tool_kwargs


# ── Models ──────────────────────────────────────────────────────────────


class QuickWebSearchCitation(BaseModel):
    """Single citation/source from Parallel Quick Web Search."""

    title: str | None = None
    url: str | None = None
    snippet: str | None = None
    publish_date: str | None = None
    excerpts: list[str] = Field(default_factory=list)


class QuickWebSearchResponse(BaseModel):
    """Response from Parallel Quick Web Search."""

    search_queries: list[str] = Field(default_factory=list)
    citations: list[QuickWebSearchCitation] = Field(default_factory=list)
    total_citations: int = 0
    search_id: str = ""
    session_id: str = ""
    warnings: list[dict[str, Any]] | None = None
    usage: list[dict[str, Any]] | None = None


# ── Helpers ─────────────────────────────────────────────────────────────


def _build_advanced_settings(
    *,
    max_results: int | None,
    include_domains: list[str] | None,
    exclude_domains: list[str] | None,
    after_date: str | None,
    location: str | None,
    max_chars_per_result: int | None,
    max_age_seconds: int | None,
    timeout_seconds: float | None,
    disable_cache_fallback: bool | None,
) -> dict[str, Any]:
    """Build the advanced_settings payload for the Parallel Search API.

    Only includes keys whose values are provided, so the API uses its
    defaults for everything else.
    """
    settings_payload: dict[str, Any] = {}

    if max_results is not None:
        settings_payload["max_results"] = max_results

    source_policy: dict[str, Any] = {}
    if include_domains:
        source_policy["include_domains"] = include_domains
    if exclude_domains:
        source_policy["exclude_domains"] = exclude_domains
    if after_date is not None:
        source_policy["after_date"] = after_date
    if source_policy:
        settings_payload["source_policy"] = source_policy

    if location is not None:
        settings_payload["location"] = location

    if max_chars_per_result is not None:
        settings_payload["excerpt_settings"] = {"max_chars_per_result": max_chars_per_result}

    fetch_policy: dict[str, Any] = {}
    if max_age_seconds is not None:
        fetch_policy["max_age_seconds"] = max_age_seconds
    if timeout_seconds is not None:
        fetch_policy["timeout_seconds"] = timeout_seconds
    if disable_cache_fallback is not None:
        fetch_policy["disable_cache_fallback"] = disable_cache_fallback
    if fetch_policy:
        settings_payload["fetch_policy"] = fetch_policy

    return settings_payload


# ── Implementation ──────────────────────────────────────────────────────


async def _quick_web_search_impl(
    search_queries: list[str],
    objective: str,
    *,
    max_results: int | None = None,
    max_chars_total: int | None = None,
    max_chars_per_result: int | None = None,
    client_model: str | None = None,
    session_id: str | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    after_date: str | None = None,
    location: str | None = None,
    max_age_seconds: int | None = None,
    timeout_seconds: float | None = None,
    disable_cache_fallback: bool | None = None,
) -> QuickWebSearchResponse:
    """Execute Parallel AI search (advanced mode) and map results.

    Args:
        search_queries: Concise keyword queries, 3-6 words each. At least
            one required; 2-3 recommended for best results (max 5).
        objective: Natural-language goal driving the search.
        max_results: Upper bound on results to return (default: 10).
        max_chars_total: Upper bound on total characters across all excerpts.
        max_chars_per_result: Upper bound on chars per single result's excerpts.
        client_model: Model consuming results, enables Parallel optimizations.
        session_id: Identifier for chaining search+extract calls in one task.
        include_domains: Restrict results to these domains only.
        exclude_domains: Exclude these domains from results.
        after_date: Only return content published on/after this date (``YYYY-MM-DD``).
        location: ISO 3166-1 alpha-2 country code for geo-targeting.
        max_age_seconds: Max cached-content age before live fetch (min 600).
        timeout_seconds: Timeout for live fetch if content needs retrieval.
        disable_cache_fallback: If True, error instead of falling back to stale cache.

    Returns:
        QuickWebSearchResponse with citations, metadata, and optional warnings/usage.

    Raises:
        ValueError: If search_queries is empty or exceeds 5, objective is blank,
            or max_age_seconds < 600.
        RuntimeError: If PARALLEL_API_KEY is not set or the API returns an error.
    """
    if not search_queries:
        raise ValueError("search_queries must contain at least 1 query (max 5).")
    if len(search_queries) > 5:
        raise ValueError("search_queries must not exceed 5 queries.")
    nonblank = [q for q in search_queries if q.strip()]
    if len(nonblank) != len(search_queries):
        raise ValueError("search_queries must not contain blank strings.")
    if not objective or not objective.strip():
        raise ValueError("objective must be a non-empty string.")
    if max_age_seconds is not None and max_age_seconds < 600:
        raise ValueError("max_age_seconds must be >= 600 (10 minutes).")

    api_key = settings.parallel_api_key
    if not api_key:
        raise RuntimeError("PARALLEL_API_KEY is not set.")

    advanced_settings = _build_advanced_settings(
        max_results=max_results,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
        after_date=after_date,
        location=location,
        max_chars_per_result=max_chars_per_result,
        max_age_seconds=max_age_seconds,
        timeout_seconds=timeout_seconds,
        disable_cache_fallback=disable_cache_fallback,
    )

    search_kwargs: dict[str, Any] = {
        "search_queries": search_queries,
        "objective": objective,
        "mode": "advanced",
    }
    if max_chars_total is not None:
        search_kwargs["max_chars_total"] = max_chars_total
    if client_model:
        search_kwargs["client_model"] = client_model
    if session_id:
        search_kwargs["session_id"] = session_id
    if advanced_settings:
        search_kwargs["advanced_settings"] = advanced_settings

    try:
        async with AsyncParallel(api_key=api_key) as client:
            result = await client.search(**search_kwargs)
    except Exception as exc:
        raise RuntimeError(f"Parallel search failed: {exc}") from exc

    citations: list[QuickWebSearchCitation] = []
    for item in result.results:
        citations.append(
            QuickWebSearchCitation(
                title=item.title,
                url=item.url,
                snippet="\n".join(item.excerpts) if item.excerpts else None,
                publish_date=item.publish_date,
                excerpts=list(item.excerpts) if item.excerpts else [],
            )
        )

    warnings: list[dict[str, Any]] | None = None
    if result.warnings:
        warnings = [w.model_dump(exclude_none=True) for w in result.warnings]

    usage: list[dict[str, Any]] | None = None
    if result.usage:
        usage = [u.model_dump(exclude_none=True) for u in result.usage]

    return QuickWebSearchResponse(
        search_queries=search_queries,
        citations=citations,
        total_citations=len(citations),
        search_id=result.search_id or "",
        session_id=result.session_id or "",
        warnings=warnings,
        usage=usage,
    )


# ── MCP Registration ────────────────────────────────────────────────────


def register_quick_web_search(mcp: Any) -> None:
    """Register the quick_web_search tool on the given FastMCP server."""

    @mcp.tool(**tool_kwargs("quick_web_search"))
    async def quick_web_search(
        search_queries: list[str],
        objective: str,
        max_results: int | None = None,
        max_chars_total: int | None = None,
        max_chars_per_result: int | None = None,
        client_model: str | None = None,
        session_id: str | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        after_date: str | None = None,
        location: str | None = None,
        max_age_seconds: int | None = None,
        timeout_seconds: float | None = None,
        disable_cache_fallback: bool | None = None,
        ctx: Context = CurrentContext(),
    ) -> dict:
        """Fast reconnaissance search using Parallel AI (advanced mode).

        When to use this tool:
        - As the initial step to scope a complex, broad, or unfamiliar topic.
        - When you need quick keyword-based discovery before deep reading.

        Key features & Sequencing:
        - Generates multiple citations with excerpts, publish dates, and usage metadata.
        - You MUST analyze these citations to identify specific, high-value URLs.
        - After calling this, you SHOULD call get_content or batch_get_content on the most relevant URLs to read their full text.
        - Do NOT use this if you need deep cross-provider RRF ranking (use web_search instead).

        Args:
            search_queries: Concise keyword queries, 3-6 words each. At least one
                required; 2-3 recommended for best results (max 5).
            objective: Research goal — what you're trying to accomplish.
           max_results: Max citations to return (1-20, default: 10).
            max_chars_total: Cap on total excerpt characters across all results.
            max_chars_per_result: Cap on characters per single result's excerpts.
            client_model: Model consuming results for Parallel optimizations.
            session_id: ID for chaining search+extract calls in one task.
            include_domains: Restrict to these domains only.
            exclude_domains: Block these domains.
            after_date: Only include content published on/after this date (``YYYY-MM-DD``).
            location: ISO country code for geo-targeted results.
            max_age_seconds: Max cached-content age before live fetch (min 600).
            timeout_seconds: Timeout for live fetch when content needs retrieval.
            disable_cache_fallback: If True, error instead of using stale cache.
        """
        await ctx.info(f"Quick web search ({len(search_queries)} queries)...")
        try:
            response = await _quick_web_search_impl(
                search_queries,
                objective,
                max_results=max_results,
                max_chars_total=max_chars_total,
                max_chars_per_result=max_chars_per_result,
                client_model=client_model,
                session_id=session_id,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
                after_date=after_date,
                location=location,
                max_age_seconds=max_age_seconds,
                timeout_seconds=timeout_seconds,
                disable_cache_fallback=disable_cache_fallback,
            )
        except Exception as exc:
            return format_tool_error(exc, provider="parallel")
        await ctx.info(f"Found {response.total_citations} citations")
        return response.model_dump(exclude_none=True)
