"""MCP stdio server for hsearch."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from hsearch.config import PROVIDER_ENV, configured_providers, get_key
from hsearch.engine import (
    extract_urls,
    search as engine_search,
    answer as engine_answer,
    ground as engine_ground,
    find_similar as engine_find_similar,
    research as engine_research,
    agent as engine_agent,
    map_site as engine_map_site,
    crawl_site as engine_crawl_site,
    account_usage as engine_account_usage,
)
from hsearch.providers import list_providers
from hsearch.schema import render_schema

# mcp 2.x renamed FastMCP -> MCPServer (mcp.server.mcpserver). The constructor,
# .tool() decorator and .run(transport=...) signatures are unchanged, so bind
# whichever class this environment provides and use it identically below.
try:  # pragma: no cover - exercised by CLI fallback when the extra is missing.
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # pragma: no cover - mcp 1.x, or the extra is absent.
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:
        FastMCP = None  # type: ignore[assignment]
        MCP_IMPORT_ERROR: ImportError | None = e
    else:
        MCP_IMPORT_ERROR = None
else:
    MCP_IMPORT_ERROR = None


INSTALL_HINT = 'Install MCP support with: pip install -e ".[mcp]"'


def _as_list(value: str | list[str] | tuple[str, ...] | None) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


async def search(
    query: str,
    mode: str | None = None,
    provider: str | list[str] | None = None,
    top: int = 10,
    time: str | None = None,
    lang: str | None = None,
    region: str | None = None,
    site: str | list[str] | None = None,
    exclude: str | list[str] | None = None,
    answer: bool = False,
    summary: bool = False,
    max_age_hours: int | None = None,
    depth: str | None = None,
    all_providers: bool = False,
    no_cache: bool = False,
    cache_ttl: int | None = None,
    extract_top: int = 0,
    extract_provider: str = "jina",
    sources: str | None = None,
    goggles: str | list[str] | None = None,
    serper_type: str | None = None,
    page: int | None = None,
    autocorrect: bool | None = None,
    livecrawl: str | None = None,
    auto: bool = False,
    raw: bool = False,
    retries: int = 2,
    days: int | None = None,
    chunks_per_source: int | None = None,
    additional_query: str | list[str] | None = None,
    highlights: bool = False,
    context_threshold: str | None = None,
    exact: bool = False,
    exa_type: str | None = None,
    include_favicon: bool = False,
    include_usage: bool = False,
    include_images: bool = False,
    include_image_descriptions: bool = False,
    answer_depth: str | None = None,
    moderation: bool = False,
    livecrawl_timeout: int | None = None,
    ignore_invalid_urls: bool = False,
    location: str | None = None,
    firecrawl_scrape_timeout: int | None = None,
    firecrawl_wait_for: int | None = None,
    jina_engine: str | None = None,
    jina_respond_with: str | None = None,
    jina_target_selector: str | None = None,
    jina_wait_for: str | None = None,
    jina_remove_selector: str | None = None,
    jina_generated_alt: bool = False,
    safe_search: bool = False,
    project_id: str | None = None,
    firecrawl_only_clean_content: bool = False,
    firecrawl_max_age: int | None = None,
    firecrawl_min_age: int | None = None,
    firecrawl_block_ads: bool | None = None,
    firecrawl_proxy: str | None = None,
    firecrawl_question: str | None = None,
    highlights_query: str | None = None,
) -> dict[str, Any]:
    """Run hsearch and return the same JSON shape as ``hsearch search -f json``."""
    resp = await engine_search(
        query,
        providers=_as_list(provider),
        mode=mode,
        all_providers=all_providers,
        top=top,
        no_cache=no_cache,
        cache_ttl=cache_ttl,
        time=time,
        lang=lang,
        region=region,
        sites=_as_list(site),
        exclude=_as_list(exclude),
        extract_top=extract_top,
        extract_provider=extract_provider,
        answer=answer,
        summary=summary,
        max_age_hours=max_age_hours,
        depth=depth,
        sources=sources,
        goggles=_as_list(goggles),
        serper_type=serper_type,
        page=page,
        autocorrect=autocorrect,
        livecrawl=livecrawl,
        auto=auto,
        raw=raw,
        retries=retries,
        days=days,
        chunks_per_source=chunks_per_source,
        additional_queries=_as_list(additional_query),
        highlights=highlights,
        context_threshold=context_threshold,
        exact=exact,
        exa_type=exa_type,
        include_favicon=include_favicon,
        include_usage=include_usage,
        include_images=include_images,
        include_image_descriptions=include_image_descriptions,
        answer_depth=answer_depth,
        moderation=moderation,
        livecrawl_timeout=livecrawl_timeout,
        ignore_invalid_urls=ignore_invalid_urls,
        location=location,
        firecrawl_scrape_timeout=firecrawl_scrape_timeout,
        firecrawl_wait_for=firecrawl_wait_for,
        jina_engine=jina_engine,
        jina_respond_with=jina_respond_with,
        jina_target_selector=jina_target_selector,
        jina_wait_for=jina_wait_for,
        jina_remove_selector=jina_remove_selector,
        jina_generated_alt=jina_generated_alt,
        safe_search=safe_search,
        project_id=project_id,
        firecrawl_only_clean_content=firecrawl_only_clean_content,
        firecrawl_max_age=firecrawl_max_age,
        firecrawl_min_age=firecrawl_min_age,
        firecrawl_block_ads=firecrawl_block_ads,
        firecrawl_proxy=firecrawl_proxy,
        firecrawl_question=firecrawl_question,
        highlights_query=highlights_query,
    )
    return resp.to_dict()


async def extract(
    url: str,
    provider: str = "jina",
    query: str | None = None,
    extract_depth: str | None = None,
    extract_format: str | None = None,
) -> dict[str, Any]:
    """Extract a URL and return the same JSON shape as ``hsearch extract -f json``.

    provider: jina | firecrawl | tavily. The tavily provider also honors
    ``query`` (rerank chunks by relevance), ``extract_depth`` (basic|advanced),
    and ``extract_format`` (markdown|text).
    """
    options: dict[str, Any] = {}
    if provider == "tavily":
        if query:
            options["query"] = query
        if extract_depth:
            options["extract_depth"] = extract_depth
        if extract_format:
            options["format"] = extract_format
    results = await extract_urls([url], provider=provider, concurrency=1, **options)
    ok = [{"url": r.url, "content": r.content} for r in results if not r.error]
    errors = {r.url: r.error for r in results if r.error}
    out: dict[str, Any] = {
        "meta": {
            "provider": provider,
            "urls_requested": 1,
            "urls_succeeded": len(ok),
        },
        "results": ok,
    }
    if errors:
        out["errors"] = errors
    return out


def providers() -> dict[str, Any]:
    """Return available providers and API-key configuration status."""
    items = [
        {
            "name": name,
            "env_var": PROVIDER_ENV[name],
            "configured": bool(get_key(name)),
        }
        for name in list_providers()
    ]
    return {"providers": items, "configured": configured_providers()}


def schema() -> dict[str, Any]:
    """Return the same tool schema as ``hsearch schema``."""
    return json.loads(render_schema())


async def answer_tool(
    query: str,
    text: bool = False,
) -> dict[str, Any]:
    """Get an LLM-generated answer with citations from Exa's /answer endpoint."""
    resp = await engine_answer(query, text=text)
    return resp.to_dict()


async def ground_tool(
    statement: str,
    no_cache: bool = False,
) -> dict[str, Any]:
    """Fact-check a statement using Jina's Grounding API (g.jina.ai)."""
    resp = await engine_ground(statement, no_cache=no_cache)
    return resp.to_dict()


async def similar_tool(
    url: str,
    top: int = 10,
    text: bool = False,
    highlights: bool = False,
    summary: bool = False,
) -> dict[str, Any]:
    """Find pages semantically similar to a given URL using Exa's /findSimilar endpoint."""
    resp = await engine_find_similar(url, top=top, text=text, highlights=highlights, summary=summary)
    return resp.to_dict()


async def research_tool(
    input_text: str,
    model: str = "mini",
    citation_format: str = "numbered",
    timeout: float = 600.0,
) -> dict[str, Any]:
    """Run a Tavily deep-research task (async agent) and return the final report with sources.

    model: mini (fast, narrow questions) | pro (deep, multi-step) | auto.
    Mini tasks usually complete in 10-60s; pro can take several minutes.
    """
    resp = await engine_research(
        input_text, model=model, citation_format=citation_format, timeout=timeout
    )
    return resp.to_dict()


async def agent_tool(
    query: str,
    effort: str = "auto",
    timeout: float = 600.0,
) -> dict[str, Any]:
    """Run an Exa Agent task (async high-compute research / list-building / enrichment).

    effort: minimal | low | medium | high | xhigh | auto. minimal/low answer
    narrow questions in seconds; high/xhigh deep multi-hop tasks take minutes.
    Returns the final text/structured output, grounding, and cost breakdown.
    """
    resp = await engine_agent(query, effort=effort, timeout=timeout)
    return resp.to_dict()


async def map_tool(
    url: str,
    max_depth: int = 1,
    limit: int = 50,
    instructions: str = "",
) -> dict[str, Any]:
    """Map a site's URL inventory via Tavily /map — fast, no content extraction.

    Best first move for COMPLETE page enumeration (marketplace catalogs, docs
    trees, connector listings) where client-side pagination hides most entries
    and sitemap.xml is missing or stale. instructions is natural-language
    steering, e.g. "only integration detail pages".
    """
    resp = await engine_map_site(
        url, max_depth=max_depth, limit=limit, instructions=instructions or None
    )
    return resp.to_dict()


async def crawl_tool(
    url: str,
    max_depth: int = 1,
    limit: int = 20,
    instructions: str = "",
    extract_depth: str = "basic",
) -> dict[str, Any]:
    """Crawl a site and extract each page's content via Tavily /crawl.

    Run map first to size the job. instructions prunes the traversal frontier
    during the crawl (real agentic steering, not a post-filter).
    """
    resp = await engine_crawl_site(
        url,
        max_depth=max_depth,
        limit=limit,
        instructions=instructions or None,
        extract_depth=extract_depth,
        format="markdown",
    )
    return resp.to_dict()


async def usage_tool() -> dict[str, Any]:
    """Remaining quota / usage for providers that expose it (Tavily, Firecrawl).

    Call this before launching an expensive multi-provider sweep so you know
    whether credits will run out mid-run. Brave/Serper/Exa/Jina have no public
    usage endpoint; Exa reports per-call costDollars in search responses.
    """
    return await engine_account_usage()


def build_server() -> Any:
    """Build a FastMCP server with hsearch tools registered."""
    if FastMCP is None:
        raise RuntimeError(INSTALL_HINT) from MCP_IMPORT_ERROR
    server = FastMCP(
        "hsearch",
        instructions="Unified search and extraction over Brave, Serper, Exa, Tavily, Firecrawl, and Jina.",
    )
    server.tool(name="search")(search)
    server.tool(name="extract")(extract)
    server.tool(name="answer")(answer_tool)
    server.tool(name="ground")(ground_tool)
    server.tool(name="similar")(similar_tool)
    server.tool(name="research")(research_tool)
    server.tool(name="agent")(agent_tool)
    server.tool(name="map")(map_tool)
    server.tool(name="crawl")(crawl_tool)
    server.tool(name="usage")(usage_tool)
    server.tool(name="providers")(providers)
    server.tool(name="schema")(schema)
    return server


def run_stdio() -> None:
    """Run the MCP server over stdio. stdout is reserved for MCP frames."""
    build_server().run(transport="stdio")
