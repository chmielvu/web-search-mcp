"""Standalone quick_web_search MCP tool backed by Parallel AI Search API.

Locks mode to ``advanced`` and exposes the full Parallel Search surface:
required ``search_queries`` + ``objective``, plus domain/location filters,
freshness, fetch policy, and excerpt sizing.  Returns rich metadata
(search_id, session_id, warnings, usage) alongside citations with
publish dates and raw excerpts.
"""

from __future__ import annotations

import logging
import time
from uuid import uuid4
from typing import Annotated, Any, Literal

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import BaseModel, Field

from ...errors import raise_tool_error
from ...models import WebSearchNext, fetch_next, make_next
from .quick_web_search_docs import fetch_docs_outcome
from .quick_web_search_youtube import fetch_youtube_outcome
from ...settings import settings
from ...tools.catalog import tool_kwargs
from ...utils.observability import emit_tool_observability_event

LOGGER = logging.getLogger(__name__)

_YOUTUBE_DEFAULT_RESULTS = 5
_DOCS_DEFAULT_RESULTS = 5
_MODE_TIMEOUT_SECONDS = 45.0
_MODE_MAX_CHARS = 12_000


def _bound_text(value: str, budget: int) -> tuple[str, bool]:
    """Truncate text to a character budget, reporting whether it truncated."""
    if len(value) <= budget:
        return value, False
    return f"{value[: max(0, budget - 1)]}…", True


async def _quick_youtube_impl(
    *,
    query: str | None,
    num_results: int | None,
    max_chars_total: int | None,
    timeout_seconds: float | None,
) -> QuickWebSearchResponse:
    """Run YouTube discovery and normalize videos into citations."""
    if query is None or not query.strip():
        raise ValueError(
            "mode='youtube' requires a non-blank query, e.g. query='fastmcp tutorial'."
        )
    count = _YOUTUBE_DEFAULT_RESULTS if num_results is None else num_results
    if not 1 <= count <= 20:
        raise ValueError("num_results must be between 1 and 20.")
    outcome = await fetch_youtube_outcome(
        query,
        max_results=count,
        timeout_seconds=timeout_seconds or _MODE_TIMEOUT_SECONDS,
    )
    budget = max_chars_total or _MODE_MAX_CHARS
    citations: list[QuickWebSearchCitation] = []
    for video in outcome.videos:
        snippet, _ = _bound_text(video.snippet, max(1, budget))
        budget -= len(snippet)
        citations.append(
            QuickWebSearchCitation(
                title=video.title,
                url=video.url,
                snippet=snippet or None,
                publish_date=video.published_at,
                excerpts=[video.snippet] if video.snippet else [],
            )
        )
    next_hints = (
        [
            make_next(
                tool="youtube_transcript",
                query={"video_id_or_url": outcome.videos[0].url},
                why="Extract the transcript for the best matching video.",
                confidence="high",
            )
        ]
        if outcome.videos
        else None
    )
    return QuickWebSearchResponse(
        search_queries=[query.strip()],
        citations=citations,
        total_citations=len(citations),
        warnings=[w.model_dump(exclude_none=True) for w in outcome.warnings] or None,
        usage=[u.model_dump(exclude_none=True) for u in outcome.usage] or None,
        next=next_hints,
    )


async def _quick_docs_impl(
    *,
    repo_url: str | None,
    question: str | None,
    context7_library_id: str | None,
    max_results: int | None,
    max_chars_total: int | None,
    timeout_seconds: float | None,
) -> QuickWebSearchResponse:
    """Run library documentation discovery and normalize it into citations."""
    if repo_url is None or not repo_url.strip():
        raise ValueError(
            "mode='docs' requires repo_url, e.g. repo_url='https://github.com/owner/repository'."
        )
    if question is None or not question.strip():
        raise ValueError("mode='docs' requires a non-blank question.")
    cap = _DOCS_DEFAULT_RESULTS if max_results is None else max_results
    if not 1 <= cap <= 50:
        raise ValueError("max_results must be between 1 and 50 for mode='docs'.")
    outcome = await fetch_docs_outcome(
        repo_url,
        question,
        context7_library_id=context7_library_id,
        max_results=cap,
        max_chars_total=max_chars_total or _MODE_MAX_CHARS,
        timeout_seconds=timeout_seconds or _MODE_TIMEOUT_SECONDS,
    )
    budget = max_chars_total or _MODE_MAX_CHARS
    excerpts: list[str] = []
    for insight in outcome.insights:
        if budget <= 0:
            break
        text, _ = _bound_text(f"**{insight.title}**\n{insight.content}", max(1, budget))
        excerpts.append(text)
        budget -= len(text)
    source_urls = [
        url
        for url in (insight.source for insight in outcome.insights)
        if isinstance(url, str) and url.startswith(("http://", "https://"))
    ]
    return QuickWebSearchResponse(
        search_queries=[question.strip()],
        citations=[
            QuickWebSearchCitation(
                title=f"{outcome.repository} documentation",
                url=outcome.repository,
                snippet=outcome.answer,
                publish_date=None,
                excerpts=excerpts,
            )
        ],
        total_citations=1,
        warnings=[w.model_dump(exclude_none=True) for w in outcome.warnings] or None,
        usage=[u.model_dump(exclude_none=True) for u in outcome.usage] or None,
        next=fetch_next(
            source_urls,
            why="Fetch the backing documentation sources for full context.",
            confidence="low",
        ),
    )


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
    next: list[WebSearchNext] | None = None


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
    try:
        from parallel import AsyncParallel
    except ModuleNotFoundError as exc:
        if exc.name != "parallel":
            raise
        raise RuntimeError(
            "Parallel SDK is unavailable; install the 'parallel-web' dependency."
        ) from exc

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

    async with AsyncParallel(api_key=api_key) as client:
        result = await client.search(**search_kwargs)

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

    next_hints = fetch_next(
        [c.url for c in citations if c.url],
        why="Excerpts are teasers; fetch the most relevant URLs for full text.",
        confidence="medium",
    )
    return QuickWebSearchResponse(
        search_queries=search_queries,
        citations=citations,
        total_citations=len(citations),
        search_id=result.search_id or "",
        session_id=result.session_id or "",
        warnings=warnings,
        usage=usage,
        next=next_hints,
    )


# ── MCP Registration ────────────────────────────────────────────────────


def register_quick_web_search(mcp: Any) -> None:
    """Register the quick_web_search tool on the given FastMCP server."""

    @mcp.tool(**tool_kwargs("quick_web_search"))
    async def quick_web_search(
        mode: Annotated[
            Literal["web", "youtube", "docs"],
            Field(description="Discovery mode: 'web' (default), 'youtube', or 'docs'."),
        ] = "web",
        search_queries: Annotated[
            list[str] | None,
            Field(
                description="Concise keyword queries, 3-6 words each (mode='web': 1-5 required)."
            ),
        ] = None,
        objective: Annotated[
            str | None,
            Field(description="Natural-language goal driving the search (mode='web' required)."),
        ] = None,
        query: Annotated[
            str | None,
            Field(description="Video search term (mode='youtube' required)."),
        ] = None,
        num_results: Annotated[
            int | None,
            Field(description="Videos to return, 1-20 (mode='youtube', default 5)."),
        ] = None,
        repo_url: Annotated[
            str | None,
            Field(
                description="Public GitHub repository URL "
                "(mode='docs' required, e.g. https://github.com/owner/repo)."
            ),
        ] = None,
        question: Annotated[
            str | None,
            Field(description="Documentation question (mode='docs' required)."),
        ] = None,
        context7_library_id: Annotated[
            str | None,
            Field(description="Explicit Context7 library ID override (mode='docs')."),
        ] = None,
        max_results: Annotated[
            int | None,
            Field(description="Upper bound on results to return (web: 1-20, default 10)."),
        ] = None,
        max_chars_total: Annotated[
            int | None, Field(description="Upper bound on total characters across all excerpts.")
        ] = None,
        max_chars_per_result: Annotated[
            int | None, Field(description="Upper bound on chars per single result's excerpts.")
        ] = None,
        client_model: Annotated[
            str | None,
            Field(description="Optional model identifier used to optimize result handling."),
        ] = None,
        session_id: Annotated[
            str | None,
            Field(description="Identifier for chaining search+extract calls in one task."),
        ] = None,
        include_domains: Annotated[
            list[str] | None, Field(description="Restrict results to these domains only.")
        ] = None,
        exclude_domains: Annotated[
            list[str] | None, Field(description="Exclude these domains from results.")
        ] = None,
        after_date: Annotated[
            str | None,
            Field(description="Only return content published on/after this date (YYYY-MM-DD)."),
        ] = None,
        location: Annotated[
            str | None, Field(description="ISO 3166-1 alpha-2 country code for geo-targeting.")
        ] = None,
        max_age_seconds: Annotated[
            int | None, Field(description="Max cached-content age before live fetch (min 600).")
        ] = None,
        timeout_seconds: Annotated[
            float | None, Field(description="Timeout for live fetch if content needs retrieval.")
        ] = None,
        disable_cache_fallback: Annotated[
            bool | None, Field(description="If true, error instead of falling back to stale cache.")
        ] = None,
        ctx: Context = CurrentContext(),
    ) -> QuickWebSearchResponse:
        """Fast first-pass discovery across the web, YouTube, or library docs.

        One tool, three modes selected by `mode`. Every mode returns the same
        citations[] shape so downstream chaining is uniform.

        WHEN TO USE:
        - mode="web" (default): initial reconnaissance for a complex, broad,
          or unfamiliar topic; fanning 3-6 word keyword queries for coverage.
        - mode="youtube": finding videos (tutorials, talks, demos) by search
          term. This replaces the removed youtube_search tool.
        - mode="docs": answering a question about a public GitHub-hosted
          library from its official docs (Context7) and repo guide (DeepWiki).
          This replaces code_search mode="docs".

        WHEN NOT TO USE:
        - Deep cross-provider RRF ranking (use web_search).
        - Grounded answer synthesis (use gemini_search).
        - Source-code implementations (use code_search; for library
          documentation use mode="docs" here, not code_search).
        - Video transcripts (use youtube_transcript; find the video with
          mode="youtube" first).
        - Full page text (use fetch on citation URLs).

        RETURNS:
        - citations[]: each with title, url, snippet, publish_date, and excerpts[].
        - total_citations: count.
        - warnings[]/usage[]: per-provider partial-failure evidence.
        - next: suggested follow-up calls (fetch, youtube_transcript).

        CHAINING:
        - web: call fetch on the most relevant citation URLs.
        - youtube: call youtube_transcript on the top video URL.
        - docs: call fetch on the backing source URLs.

        PARAM RULES BY MODE (violations raise actionable errors):
        - web: search_queries (1-5 non-blank) and objective are required.
        - youtube: query is required; num_results 1-20 (default 5).
        - docs: repo_url (https://github.com/owner/repo) and question required.

        Args:
            mode: Discovery mode: 'web' (default), 'youtube', or 'docs'.
            search_queries: Concise keyword queries, 3-6 words each (web only).
            objective: Natural-language goal driving the search (web only).
            query: Video search term (youtube only).
            num_results: Videos to return, 1-20, default 5 (youtube only).
            repo_url: Public GitHub repository URL (docs only).
            question: Documentation question (docs only).
            context7_library_id: Explicit Context7 library ID override (docs only).
            max_results: Upper bound on results (web: 1-20, default 10).
            max_chars_total: Cap on total excerpt characters across all results.
            max_chars_per_result: Cap on characters per single result's excerpts.
            client_model: Model consuming results; enables provider-side
                optimizations.
            session_id: ID for chaining search+extract calls in one task.
            include_domains: Restrict results to these domains only.
            exclude_domains: Block these domains.
            after_date: Only include content published on/after this date
                (YYYY-MM-DD).
            location: ISO 3166-1 alpha-2 country code for geo-targeting.
            max_age_seconds: Max cached-content age before live fetch (min 600).
            timeout_seconds: Timeout for live fetch when content needs retrieval.
            disable_cache_fallback: If true, error instead of falling back to
                stale cache.
        """
        started = time.monotonic()
        tool_call_id = str(uuid4())
        provider = {"web": "parallel", "youtube": "youtube", "docs": "documentation"}[mode]
        emit_tool_observability_event(
            LOGGER,
            "quick_web_search",
            "request",
            tool_call_id=tool_call_id,
            mode=mode,
            search_queries=search_queries,
            objective=objective,
            query=query,
            repo_url=repo_url,
            question=question,
            session_id=session_id,
            max_results=max_results,
            num_results=num_results,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            client_model=client_model,
        )
        await ctx.info(f"Quick web search (mode={mode})...")
        try:
            if mode == "youtube":
                response = await _quick_youtube_impl(
                    query=query,
                    num_results=num_results,
                    max_chars_total=max_chars_total,
                    timeout_seconds=timeout_seconds,
                )
            elif mode == "docs":
                response = await _quick_docs_impl(
                    repo_url=repo_url,
                    question=question,
                    context7_library_id=context7_library_id,
                    max_results=max_results,
                    max_chars_total=max_chars_total,
                    timeout_seconds=timeout_seconds,
                )
            else:
                response = await _quick_web_search_impl(
                    search_queries or [],
                    objective or "",
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
            emit_tool_observability_event(
                LOGGER,
                "quick_web_search",
                "error",
                tool_call_id=tool_call_id,
                mode=mode,
                search_queries=search_queries,
                objective=objective,
                error_type=type(exc).__name__,
                error_message=str(exc),
                duration_ms=(time.monotonic() - started) * 1000,
            )
            raise_tool_error(exc, provider=provider)
        await ctx.info(f"Found {response.total_citations} citations")
        emit_tool_observability_event(
            LOGGER,
            "quick_web_search",
            "response",
            tool_call_id=tool_call_id,
            mode=mode,
            search_id=getattr(response, "search_id", None),
            provider_session_id=getattr(response, "provider_session_id", None),
            session_id=session_id,
            search_queries=search_queries,
            objective=objective,
            max_results=max_results,
            max_chars_total=max_chars_total,
            max_chars_per_result=max_chars_per_result,
            client_model=client_model,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            after_date=after_date,
            location=location,
            max_age_seconds=max_age_seconds,
            timeout_seconds=timeout_seconds,
            disable_cache_fallback=disable_cache_fallback,
            citations=response.citations,
            total_citations=response.total_citations,
            warnings=response.warnings,
            usage=response.usage,
            provider=provider,
            duration_ms=(time.monotonic() - started) * 1000,
        )
        return response
