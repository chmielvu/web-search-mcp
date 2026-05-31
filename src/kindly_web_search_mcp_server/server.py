# ruff: noqa: E402
from __future__ import annotations

# Load .env file before any other imports that read environment variables
from dotenv import load_dotenv
from pathlib import Path

# Look for .env in the package directory and parent directories
_package_dir = Path(__file__).parent
_project_root = _package_dir.parent.parent  # web-search-mcp root
load_dotenv(_project_root / ".env")
load_dotenv()  # Also try cwd as fallback

# Initialize OpenTelemetry BEFORE any other imports
# This ensures all HTTP calls (httpx, etc.) are auto-instrumented
from .telemetry import (
    init_telemetry,
    SEARCH_QUERY,
    SEARCH_NUM_RESULTS_REQUESTED,
    record_mcp_tool_call,
    record_tool_details,
    record_gemini_search,
    record_perplexity_search,
    record_youtube_transcript,
    record_youtube_search,
)
from opentelemetry import trace

init_telemetry(service_name="web-search-mcp")

import argparse
import asyncio
import json
import httpx
import logging
import os
import sys
from typing import Literal

from fastmcp import FastMCP
from fastmcp.dependencies import CurrentContext  # For context injection
from fastmcp.prompts import Message
from fastmcp.server.context import Context  # Context type
from mcp.types import ToolAnnotations  # For tool annotations

from .models import (
    BatchGetContentResponse,
    DiscoverLinksResponse,
    GetContentResponse,
    YouTubeTranscriptResponse,
    YouTubeSearchResponse,
)
from .errors import classify_error, format_tool_error
from .content.batch_orchestrator import BatchParams, run_batch_fetch
from .content.fetch_pipeline import fetch_content_artifact
from .content.link_discovery import discover_links as discover_page_links
from .content.options import build_fetch_options
from .content.summary import create_summary
from .content.windowing import slice_content
from .composio_tools import register_composio_tools
from .content.youtube import (
    YouTubeError,
    parse_youtube_url,
    fetch_transcript_data,
    format_transcript_text,
    format_transcript_timestamped,
    calculate_total_duration,
)
from .search.youtube import search_youtube_videos, YouTubeSearchError
from .search.orchestrator import run_web_search
from .cache import (
    SemanticCacheStore,
    get_semantic_cache,
    set_semantic_cache,
    classify_content_type,
    get_query_cache,
    provider_cache_key,
    get_page_cache,
)
from .search.gemini_search_tool import gemini_search_with_grounding
from .search.normalize import normalize_query, canonicalize_url
from .search.options import build_search_identity_key, build_search_options
from .settings import settings
from .utils.public_output import serialize_public_web_search_response
from .utils.diagnostics import (
    Diagnostics,
    diagnostics_enabled,
    mask_env_values,
    new_request_id,
)
from .utils.logging import configure_logging
from .utils.observability import (
    emit_tool_observability_event,
)
from .utils.singleflight import SingleFlight

configure_logging()
LOGGER = logging.getLogger(__name__)

# Singleton cache store (lazy init)
_CACHE_STORE: SemanticCacheStore | None = None

# SingleFlight for request coalescing
_search_flight = SingleFlight()
_academic_search_flight = SingleFlight()


def _get_cache_store() -> SemanticCacheStore:
    """Get or create the semantic cache store singleton."""
    global _CACHE_STORE
    if _CACHE_STORE is None:
        _CACHE_STORE = SemanticCacheStore(db_path=settings.lancedb_dir)
        LOGGER.info(f"Initialized semantic cache store at {settings.lancedb_dir}")
    return _CACHE_STORE


def _record_tool_success(
    tool_name: str,
    *,
    input_query: str | None = None,
    input_url_count: int | None = None,
    output_result_count: int | None = None,
    output_content: str | None = None,
    output_transcript: str | None = None,
) -> None:
    record_mcp_tool_call(tool_name, success=True)
    record_tool_details(
        tool_name=tool_name,
        input_query_length=len(input_query) if input_query is not None else None,
        input_url_count=input_url_count,
        output_result_count=output_result_count,
        output_content_length=len(output_content)
        if output_content is not None
        else None,
        output_transcript_length=len(output_transcript)
        if output_transcript is not None
        else None,
    )


def _record_tool_failure(tool_name: str) -> None:
    record_mcp_tool_call(tool_name, success=False)


mcp = FastMCP(
    "kindly-web-search",
    instructions=(
        "Tool routing: use web_search first for normal web discovery and keep rewrite=true by default. "
        "Use rewrite=false only for exact literals such as stack traces, quoted errors, URLs, versions, hashes, and UUIDs. "
        "Use get_content for one known URL; use batch_get_content for 3 or more URLs and follow has_more/cursor or window.next_offset. "
        "Use discover_links when you already have a URL and want outgoing links or sitemap targets. "
        "Use gemini_search for quick grounded synthesis; use perplexity_search only after refining a single-topic query. "
        "Use academic_search for scholarly papers (Semantic Scholar + ArXiv) with filters for year, venue, field of study, and open access. "
        "Use youtube_search before youtube_transcript, and composio_similarlinks to expand from a known good URL."
    ),
)

# Add expensive tool protection middleware for perplexity_search
# Implements "think first, then call expensive tool" pattern
from .middleware import create_expensive_tool_middleware

mcp.add_middleware(create_expensive_tool_middleware())

# Add differentiated rate limiting:
# - Higher throughput for lightweight tools (web_search/get_content/gemini_search)
# - Stricter quota for expensive tool (perplexity_search)
from .middleware import create_differentiated_rate_limit_middleware

mcp.add_middleware(
    create_differentiated_rate_limit_middleware(
        cheap_rps=settings.rate_limit_cheap_rps,
        cheap_burst=settings.rate_limit_cheap_burst,
        expensive_rps=settings.rate_limit_expensive_rps,
        expensive_burst=settings.rate_limit_expensive_burst,
    )
)

# Add dynamic per-tool guidance middleware (result-aware, non-blocking)
from .middleware import create_dynamic_guidance_middleware

mcp.add_middleware(create_dynamic_guidance_middleware())
register_composio_tools(mcp)

Transport = Literal["stdio", "sse", "streamable-http"]


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-web-search",
        description="MCP server: Multi-provider web search (SearXNG/Tavily/Brave/Jina) with RRF merge.",
    )

    # Accept `start-mcp-server` as a no-op positional arg for compatibility
    # with the `kindly-web-search` entry point when launched by MCP clients
    # that append the subcommand from the CLI-wrapper entry point.
    parser.add_argument(
        "_start_command",
        nargs="?",
        choices=("start-mcp-server",),
        help=argparse.SUPPRESS,
    )

    transport_group = parser.add_mutually_exclusive_group()
    transport_group.add_argument(
        "--transport",
        choices=("stdio", "sse", "streamable-http"),
        help="Transport to use (default: stdio).",
    )
    transport_group.add_argument(
        "--stdio",
        dest="transport",
        action="store_const",
        const="stdio",
        help="Run using stdio transport (default).",
    )
    transport_group.add_argument(
        "--sse",
        dest="transport",
        action="store_const",
        const="sse",
        help="Run using SSE transport.",
    )
    transport_group.add_argument(
        "--http",
        "--streamable-http",
        dest="transport",
        action="store_const",
        const="streamable-http",
        help="Run using Streamable HTTP transport.",
    )

    parser.add_argument(
        "--host",
        default=None,
        help="Bind host for HTTP/SSE transports (overrides FASTMCP_HOST).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port for HTTP/SSE transports (overrides FASTMCP_PORT).",
    )
    parser.add_argument(
        "--mount-path",
        default=None,
        help="Mount path for SSE transport (if supported by the runtime).",
    )
    return parser


def _resolve_transport(raw: str | None) -> Transport:
    if raw in ("stdio", "sse", "streamable-http"):
        return raw
    return "stdio"


def _resolve_host_port(host: str | None, port: int | None) -> tuple[str, int]:
    resolved_host = host or os.environ.get("FASTMCP_HOST", "127.0.0.1")
    resolved_port_raw = (
        str(port) if port is not None else os.environ.get("FASTMCP_PORT", "8000")
    )
    try:
        resolved_port = int(resolved_port_raw)
    except ValueError:
        resolved_port = 8000
    return resolved_host, resolved_port


def main(argv: list[str] | None = None) -> None:
    """
    Entrypoint for running the MCP server.

    Notes:
    - Many MCP clients run servers via stdio by default.
    - HTTP/SSE transports are useful for containerized and gateway deployments.
    - FastMCP does not parse CLI args by itself; we do it here.
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    transport = _resolve_transport(args.transport)

    if (
        transport == "stdio"
        and sys.stdin.isatty()
        and os.environ.get("MCP_ALLOW_TTY_STDIO", "").strip().lower()
        not in ("1", "true", "yes")
    ):
        print(
            "Error: `--stdio` transport is intended to be launched by an MCP client (stdin/stdout JSON-RPC).",
            file=sys.stderr,
        )
        print(
            "Tip: for manual testing, run with `--http` (Streamable HTTP) instead.",
            file=sys.stderr,
        )
        print(
            "Override: set MCP_ALLOW_TTY_STDIO=1 to force stdio even when stdin is a TTY.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    if not (
        os.environ.get("SEARXNG_BASE_URL", "").strip()
        or os.environ.get("TAVILY_API_KEY", "").strip()
        or os.environ.get("BRAVE_API_KEY", "").strip()
        or os.environ.get("JINA_API_KEY", "").strip()
        or (
            os.environ.get("COMPOSIO_API_KEY", "").strip()
            and os.environ.get("KINDLY_COMPOSIO_USER_ID", "").strip()
        )
        or settings.gemini_api_key.strip()
    ):
        # Do not hard-fail on startup: many clients set env vars in their MCP config
        # and expect the server to at least come up for tool discovery.
        LOGGER.warning(
            "No search provider configured (SEARXNG_BASE_URL, TAVILY_API_KEY, BRAVE_API_KEY, "
            "JINA_API_KEY, COMPOSIO_API_KEY + KINDLY_COMPOSIO_USER_ID, "
            "or KINDLY_GEMINI_API_KEY); "
            "`web_search` calls will fail until one is provided."
        )

    if transport in ("sse", "streamable-http"):
        host, port = _resolve_host_port(args.host, args.port)
        # FastMCP settings are the source of truth for host/port in HTTP transports.
        # We mutate them at runtime to allow env/CLI overrides even if defaults were
        # passed during FastMCP initialization.
        for key, value in (("host", host), ("port", port)):
            if hasattr(mcp, "settings") and hasattr(mcp.settings, key):
                setattr(mcp.settings, key, value)

    try:
        mcp.run(transport=transport, mount_path=args.mount_path)
    except TypeError:
        # Backward-compat: older MCP SDKs may not accept `mount_path`.
        mcp.run(transport=transport)


def _get_int_env(key: str, default: int) -> int:
    raw = (os.environ.get(key) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _get_float_env(key: str, default: float) -> float:
    raw = (os.environ.get(key) or "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _resolve_tool_total_timeout_seconds() -> float:
    """
    Resolve the total per-tool time budget (seconds).

    Historically this was clamped to <=55s to stay below common 60s tool-call limits.
    In practice, Windows headless-browser cold starts can exceed that, so we allow a
    higher cap that can be tuned via environment variables.
    """
    value = _get_float_env("KINDLY_TOOL_TOTAL_TIMEOUT_SECONDS", 120.0)
    max_value = _get_float_env("KINDLY_TOOL_TOTAL_TIMEOUT_MAX_SECONDS", 600.0)
    safe_max = max(1.0, max_value)
    return max(1.0, min(value, safe_max))


def _timeout_markdown_note(url: str, *, scope: str | None = None) -> str:
    detail = f": {scope}" if scope else ""
    return f"_Failed to retrieve page content: TimeoutError{detail}_\n\nSource: {url}\n"


def _resolve_web_search_max_concurrency(num_results: int) -> int:
    raw_env = (os.environ.get("KINDLY_WEB_SEARCH_MAX_CONCURRENCY") or "").strip()
    value: int | None = None
    if raw_env:
        try:
            parsed = int(raw_env)
        except ValueError:
            parsed = None
        if parsed and parsed > 0:
            value = parsed

    if value is None:
        value = 1 if os.name == "nt" else 3
    value = max(1, min(value, 5))
    if num_results > 0:
        value = min(value, num_results)
    return value


def _normalize_lightweight_search_response(response: dict, *, query: str) -> dict:
    """Return the public web_search response shape for cache and tool output."""
    normalized = serialize_public_web_search_response(response)
    normalized["query"] = query
    return normalized


def _apply_domain_filters(
    results: list[dict],
    domain_boost: list[str] | None = None,
    domain_block: list[str] | None = None,
) -> list[dict]:
    """Apply domain boost and block filters to search results.

    Args:
        results: List of search result dicts (each must have a ``link`` key).
        domain_boost: Domains to boost (move to front, preserving relative order).
        domain_block: Domains to exclude (remove entirely).

    Returns:
        Filtered and boosted results list.
    """
    if not domain_boost and not domain_block:
        return results

    from urllib.parse import urlparse

    def _url_matches_domain(url: str, pattern: str) -> bool:
        """Check if URL matches domain pattern (supports subdomains and paths)."""
        try:
            parsed = urlparse(url)
            hostname = (parsed.hostname or "").lower().replace("www.", "")
            pathname = parsed.path.lower()

            if "/" in pattern:
                pat_domain, *pat_parts = pattern.split("/")
                pat_domain = pat_domain.lower().replace("www.", "")
                pat_path = "/" + "/".join(pat_parts).lower()
                return (
                    hostname == pat_domain or hostname.endswith(f".{pat_domain}")
                ) and pathname.startswith(pat_path)

            pattern_clean = pattern.lower().replace("www.", "")
            return hostname == pattern_clean or hostname.endswith(f".{pattern_clean}")
        except Exception:
            return False

    if domain_block:
        results = [
            r
            for r in results
            if not any(_url_matches_domain(r.get("link", ""), p) for p in domain_block)
        ]

    if domain_boost:
        boosted = [
            r
            for r in results
            if any(_url_matches_domain(r.get("link", ""), p) for p in domain_boost)
        ]
        normal = [
            r
            for r in results
            if not any(_url_matches_domain(r.get("link", ""), p) for p in domain_boost)
        ]
        results = boosted + normal

    return results


@mcp.tool(
    annotations=ToolAnnotations(
        title="Web Search",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def web_search(
    query: str,
    research_goal: str,
    num_results: int = 5,
    rewrite: bool = True,
    providers: list[str] | None = None,
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
) -> dict:
    """Search the web and return lightweight results only.

    Key instruction:
    Default to this tool for web discovery. Keep rewrite=True for normal discovery.
    Set rewrite=False only for exact-literal queries: stack traces, quoted error
    messages, URLs, package versions, hashes, UUIDs, CLI flags, function names, or
    other strings that must not be paraphrased.

    When to use:
    Especially useful for coding agents like Claude Code / Codex when you need up-to-date information.
    - Debug an error by searching the exact message/stack trace (often best in quotes).
    - Double-check API signatures, interfaces, and breaking changes in official docs.
    - Confirm current package versions, release notes, and migration guides.
    - Find GitHub issues / StackOverflow threads / authoritative references for a topic.

    When not to use:
    - If you already have a specific URL to read -> use `get_content(url)` instead.

    Args:
    - query: Search query string. Prefer specific keywords and exact error text when applicable.
    - research_goal: REQUIRED. Describe what information you are looking for and why. Include:
      - The specific topic, feature, or problem you're researching
      - Any relevant context (package names, versions, error types)
      - What you plan to do with the results (implement, debug, compare, etc.)
      Example: "Find React 18.2.0 changelog to check if hooks API changed" or
      "Debug TypeError in FastAPI middleware - need solution for production"
    - num_results: Number of results to return. Default is 5; recommended range is 3-7.
      Results are diversity-pruned so 5-7 provides broad coverage without duplicates. Max 10.
    - rewrite: If True, use Mistral to generate additional search queries and merge the results.
      Standard is True. Set False for exact literals that must stay byte-stable.
    - providers: Optional list of providers to include. Examples: ["tavily"], ["brave", "jina"].
      - Standard providers (searxng, ddg, gemini) fire automatically when configured.
      - Conditional providers only fire when listed here.
      - Available providers: searxng, ddg, tavily, brave, jina, gemini, composio_llm_search.
    - result_offset: Zero-based result window offset for tool-side pagination.
    - searxng_categories: Optional SearXNG category override.
    - searxng_engines: Optional SearXNG engine override list.
    - searxng_language: Optional SearXNG language override.
    - searxng_pageno: SearXNG result page number override.
    - searxng_time_range: Optional SearXNG time range override (`day`, `week`, `month`, `year`).
    - searxng_safesearch: Optional SearXNG safesearch override (`0`, `1`, or `2`).
    - site_filters: Optional query restrictions applied to all providers.
    - domain_filters: Optional domain restrictions applied to all providers.
    - domain_boost: Optional list of domains to boost in results (e.g., ["stackoverflow.com", "github.com"]).
      Boosted domains are moved to the front of results after reranking.
      Supports subdomain matching (e.g., "reddit.com" matches "old.reddit.com").
      Supports path-aware matching (e.g., "reddit.com/r/programming" matches that subreddit).
    - domain_block: Optional list of domains to exclude from results (e.g., ["pinterest.com", "quora.com"]).
      Blocked domains are completely removed from results.
      Supports the same matching rules as domain_boost.
    - ctx: FastMCP context (auto-injected, used for logging).

    Prerequisites:
    - Requires at least one configured search provider in the server environment:
      `SEARXNG_BASE_URL` (SearXNG, primary), `KINDLY_GEMINI_API_KEY`,
      `TAVILY_API_KEY`, `BRAVE_API_KEY`, `JINA_API_KEY`, or
      `COMPOSIO_API_KEY` + `KINDLY_COMPOSIO_USER_ID`.
      If none is set, this tool will fail.

    Returns:
    - `{"query": str, "results": [{"title": str, "link": str, "snippet": str, ...}, ...]}`
    - Results are lightweight search hits only. Page content is intentionally omitted.

    Notes:
    - Provider priority: SearXNG + DDG + Gemini (standard) → Conditional providers on request.
    - Results merged via Weighted Reciprocal Rank Fusion (RRF) for optimal ranking.
    - Treat `provider_count` on each result as an agreement signal: higher means
      more configured providers surfaced the same URL.
    - If all search providers fail, the tool will error.
    - For a deeper look at one result, call `get_content()` on the chosen `link`.
    """

    # Enforce bounds
    num_results = max(1, min(num_results, 10))
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
    search_identity_key = build_search_identity_key(providers, search_options)

    # Create root span for entire web_search operation
    tracer = trace.get_tracer("web-search-mcp")
    with tracer.start_as_current_span(
        "mcp.tool.web_search",
        kind=trace.SpanKind.SERVER,
        attributes={
            SEARCH_QUERY: query[:500],
            SEARCH_NUM_RESULTS_REQUESTED: num_results,
            "search.rewrite_enabled": str(rewrite).lower(),
            "search.providers_requested": str(providers or []),
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
            providers_requested=providers or [],
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
                LOGGER.debug(f"Exact query cache hit for: {query[:100]}")
                root_span.set_attribute("cache.hit", "exact")
                root_span.set_attribute(
                    "search.num_results_returned", len(exact_cached.get("results", []))
                )
                exact_response = _normalize_lightweight_search_response(
                    exact_cached, query=query
                )
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
                _record_tool_success(
                    "web_search",
                    input_query=query,
                    output_result_count=len(exact_response.get("results", [])),
                )
                return exact_response
        except Exception as e:
            LOGGER.warning(f"Exact query cache lookup failed: {e}")

        # 2. Semantic cache lookup (if enabled, fallback for fuzzy matches)
        if settings.semantic_cache_enabled:
            try:
                cache_store = _get_cache_store()
                cached = await get_semantic_cache(
                    cache_store,
                    query,
                    min_score=settings.semantic_cache_min_score,
                    provider_key=search_identity_key,
                )
                if cached:
                    LOGGER.debug(f"Cache hit for query: {query[:100]}")
                    cached_response = cached.get("answer_json")
                    if cached_response:
                        parsed = json.loads(cached_response)
                        root_span.set_attribute("cache.hit", "semantic")
                        root_span.set_attribute(
                            "search.num_results_returned",
                            len(parsed.get("results", [])),
                        )
                        semantic_response = _normalize_lightweight_search_response(
                            parsed, query=query
                        )
                        emit_tool_observability_event(
                            LOGGER,
                            "web_search",
                            "response",
                            cache_hit="semantic",
                            query=query,
                            normalized_query=normalized_query,
                            research_goal=research_goal,
                            result_count=len(semantic_response.get("results", [])),
                            providers_used=semantic_response.get("providers_used", []),
                            warnings=semantic_response.get("warnings", []),
                            results=semantic_response.get("results", []),
                            result_window=semantic_response.get("result_window"),
                        )
                        _record_tool_success(
                            "web_search",
                            input_query=query,
                            output_result_count=len(
                                semantic_response.get("results", [])
                            ),
                        )
                        return semantic_response
            except Exception as e:
                LOGGER.warning(f"Cache lookup failed: {e}")

        root_span.set_attribute("cache.hit", "miss")

        # Report progress: rewriting and searching
        if rewrite:
            await ctx.report_progress(
                progress=20, total=100, message="Rewriting query..."
            )
        else:
            await ctx.report_progress(
                progress=20, total=100, message="Querying providers..."
            )

        diag_enabled = diagnostics_enabled()

        # Report progress: executing search
        await ctx.report_progress(
            progress=35, total=100, message="Querying providers..."
        )

        # SingleFlight: coalesce identical concurrent searches into one execution
        flight_key = SingleFlight.make_key(
            normalized_query, num_results, rewrite, search_identity_key
        )

        async def _execute_search() -> dict:
            parent_request_id = new_request_id() if diag_enabled else ""
            parent_diag = Diagnostics(
                parent_request_id, diag_enabled, stream=sys.stderr
            )
            if diag_enabled:
                env_snapshot = {
                    "SEARXNG_BASE_URL": os.environ.get("SEARXNG_BASE_URL", ""),
                    "TAVILY_API_KEY": os.environ.get("TAVILY_API_KEY", ""),
                    "BRAVE_API_KEY": os.environ.get("BRAVE_API_KEY", ""),
                    "JINA_API_KEY": os.environ.get("JINA_API_KEY", ""),
                    "COMPOSIO_API_KEY": os.environ.get("COMPOSIO_API_KEY", ""),
                    "GITHUB_TOKEN": os.environ.get("GITHUB_TOKEN", ""),
                    "KINDLY_TOOL_TOTAL_TIMEOUT_SECONDS": os.environ.get(
                        "KINDLY_TOOL_TOTAL_TIMEOUT_SECONDS", ""
                    ),
                    "KINDLY_TOOL_TOTAL_TIMEOUT_MAX_SECONDS": os.environ.get(
                        "KINDLY_TOOL_TOTAL_TIMEOUT_MAX_SECONDS", ""
                    ),
                    "KINDLY_WEB_SEARCH_MAX_CONCURRENCY": os.environ.get(
                        "KINDLY_WEB_SEARCH_MAX_CONCURRENCY", ""
                    ),
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
                providers=providers,
                research_goal=research_goal,
                search_options=search_options,
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
                LOGGER.debug(f"Stored exact query cache for: {query[:100]}")
            except Exception as e:
                LOGGER.warning(f"Exact query cache write failed: {e}")

            # Cache write: semantic cache (non-blocking to avoid delaying response)
            if settings.semantic_cache_enabled:
                try:
                    cache_store = _get_cache_store()
                    content_type = classify_content_type(query)

                    async def _safe_cache_write() -> None:
                        try:
                            await set_semantic_cache(
                                cache_store,
                                query,
                                _response,
                                content_type,
                                provider_key=search_identity_key,
                            )
                        except Exception as e:
                            LOGGER.warning(
                                "Background semantic cache write failed: %s", e
                            )

                    asyncio.create_task(_safe_cache_write())
                    LOGGER.debug(f"Scheduled semantic cache write for: {query[:100]}")
                except Exception as e:
                    LOGGER.warning(f"Semantic cache write scheduling failed: {e}")

            return _response

        response = await _search_flight.do(flight_key, _execute_search)

        if domain_boost or domain_block:
            response["results"] = _apply_domain_filters(
                response.get("results", []), domain_boost, domain_block
            )

        # Add final span attributes
        root_span.set_attribute(
            "search.num_results_returned", len(response.get("results", []))
        )
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


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Content",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def get_content(
    url: str,
    char_offset: int = 0,
    char_length: int = 20_000,
    summary_mode: str = "none",
    focus_query: str | None = None,
    include_metadata: bool = True,
    include_links: bool = False,
    max_links: int = 25,
    strip_selectors: str | None = None,
    ctx: Context = CurrentContext(),
) -> dict:
    """Fetch one URL with bounded windowing and structured status.

    When to use:
    - You already have a URL from the user or from `web_search(...)`.
    - You need source text from one page/document with continuation metadata.
    - You want optional source-grounded summary via `summary_mode`.

    When not to use:
    - If you need to discover relevant URLs first -> use `web_search(query)`.

    Args:
    - url: The URL to fetch.
    - char_offset: Character offset into the extracted source text. Default 0.
    - char_length: Maximum characters to return for this page. Default 20000.
    - summary_mode: `none`, `brief`, or `detailed`. Summaries use Chutes API when requested.
    - focus_query: Optional focus for summary generation.
    - include_metadata: Include extracted page metadata in the response.
    - include_links: Include extracted links in the response.
    - max_links: Maximum number of links to extract when include_links is true.
    - strip_selectors: Optional CSS selectors to remove before extraction.

    Returns:
    - `input_url`: exact URL provided by caller.
    - `normalized_url`: normalized URL used for cache lookup/storage and batch deduplication.
    - `fetched_url`: actual URL reached after redirects, if network fetch reached one.
    - `status`: success, partial, blocked, unsupported, or error.
    - `source_type`: detected source family such as html, pdf, github_issue, or wikipedia.
    - `fetch_backend`: backend strategy used, such as safe_http_extract, jina_reader, or browser_fallback.
    - `page_content`: bounded Markdown/text window.
    - `window`: pagination metadata with `has_more` and `next_offset`.
    - `metadata`: optional page metadata such as title, description, canonical URL, and domain.
    - `links`: optional discovered links when `include_links=True`.
    - `continuation_notice`: human-readable truncation notice when the returned window is partial.
    """

    await ctx.report_progress(progress=5, total=100, message="Checking page cache...")
    await ctx.info(f"Fetching: {url[:80]}...")
    emit_tool_observability_event(
        LOGGER,
        "get_content",
        "request",
        url=url,
        char_offset=char_offset,
        char_length=char_length,
        summary_mode=summary_mode,
        focus_query=focus_query,
        include_metadata=include_metadata,
        include_links=include_links,
        max_links=max_links,
        strip_selectors=strip_selectors,
    )

    max_length = _get_int_env("KINDLY_GET_CONTENT_MAX_CHARS", 50_000)
    safe_length = max(1, min(char_length, max_length))
    safe_offset = max(0, char_offset)
    safe_summary_mode = (
        summary_mode if summary_mode in {"none", "brief", "detailed"} else "none"
    )
    fetch_options = build_fetch_options(
        include_metadata=include_metadata,
        include_links=include_links,
        max_links=max_links,
        strip_selectors=strip_selectors,
    )

    artifact = None
    normalized_url = canonicalize_url(url)
    try:
        cached = get_page_cache().lookup(normalized_url)
        if cached:
            cached_metadata = cached.get("metadata")
            cached_page_metadata = (
                cached_metadata.get("metadata")
                if isinstance(cached_metadata, dict) and "metadata" in cached_metadata
                else cached_metadata
            )
            cached_links = (
                cached_metadata.get("links")
                if isinstance(cached_metadata, dict)
                else None
            )
            artifact = {
                "input_url": url,
                "normalized_url": normalized_url,
                "fetched_url": None,
                "status": "success",
                "source_type": "cache",
                "fetch_backend": cached.get("extraction_method") or "cache",
                "content_type": "text/markdown",
                "markdown": cached["page_content"],
                "metadata": cached_page_metadata,
                "links": cached_links,
                "error": None,
            }
    except Exception as exc:
        LOGGER.warning(f"Page cache lookup failed: {exc}")

    if artifact is None:
        await ctx.report_progress(
            progress=30, total=100, message="Resolving content..."
        )
        fetched = None
        try:
            fetch_coro = fetch_content_artifact(url, fetch_options=fetch_options)
            try:
                fetched = await asyncio.wait_for(
                    fetch_coro,
                    timeout=_resolve_tool_total_timeout_seconds(),
                )
            except TypeError as exc:
                if "fetch_options" not in str(exc):
                    raise
                fetched = await asyncio.wait_for(
                    fetch_content_artifact(url),
                    timeout=_resolve_tool_total_timeout_seconds(),
                )
        except asyncio.TimeoutError:
            artifact = {
                "input_url": url,
                "normalized_url": normalized_url,
                "fetched_url": None,
                "status": "error",
                "source_type": "unknown",
                "fetch_backend": "timeout",
                "content_type": None,
                "markdown": "",
                "metadata": None,
                "links": None,
                "error": {
                    "code": "timeout",
                    "message": "Content fetch exceeded the configured tool time budget.",
                    "retryable": True,
                },
            }
        except Exception as exc:
            artifact = {
                "input_url": url,
                "normalized_url": normalized_url,
                "fetched_url": None,
                "status": "error",
                "source_type": "unknown",
                "fetch_backend": "fetch_pipeline",
                "content_type": None,
                "markdown": "",
                "metadata": None,
                "links": None,
                "error": {
                    "code": type(exc).__name__,
                    "message": str(exc),
                    "retryable": True,
                },
            }
        if fetched is not None:
            artifact = {
                "input_url": fetched.input_url,
                "normalized_url": fetched.normalized_url,
                "fetched_url": fetched.fetched_url,
                "status": fetched.status,
                "source_type": fetched.source_type,
                "fetch_backend": fetched.fetch_backend,
                "content_type": fetched.content_type,
                "markdown": fetched.markdown,
                "metadata": fetched.metadata,
                "links": fetched.links if include_links else None,
                "error": None
                if fetched.error is None
                else {
                    "code": fetched.error.code,
                    "message": fetched.error.message,
                    "retryable": fetched.error.retryable,
                },
            }
        if fetched is not None and fetched.status == "success" and fetched.markdown:
            try:
                get_page_cache().store(
                    canonical_url=fetched.normalized_url,
                    page_content=fetched.markdown,
                    extraction_method=fetched.fetch_backend,
                    metadata={
                        "metadata": fetched.metadata,
                        "links": fetched.links,
                    },
                )
            except Exception as exc:
                LOGGER.warning(f"Page cache store failed: {exc}")

    windowed = slice_content(
        artifact["markdown"],
        offset=safe_offset,
        length=safe_length,
    )
    summary = await create_summary(
        windowed.content, mode=safe_summary_mode, focus_query=focus_query
    )

    response = GetContentResponse(
        input_url=url,
        normalized_url=artifact["normalized_url"],
        fetched_url=artifact["fetched_url"],
        status=artifact["status"],
        source_type=artifact["source_type"],
        fetch_backend=artifact["fetch_backend"],
        page_content=windowed.content,
        window=windowed.window.__dict__,
        metadata=artifact.get("metadata") if include_metadata else None,
        links=artifact.get("links") if include_links else None,
        continuation_notice=windowed.window.continuation_notice,
        content_type=artifact["content_type"],
        error=artifact["error"],
        summary=summary,
    ).model_dump(exclude_none=True)
    response.setdefault("fetched_url", None)

    await ctx.report_progress(progress=100, total=100, message="Done")
    await ctx.info(
        f"Fetched status={response['status']} chars={len(response['page_content'])} has_more={response['window']['has_more']}"
    )
    emit_tool_observability_event(
        LOGGER,
        "get_content",
        "response",
        url=url,
        status=response["status"],
        content_length=len(response["page_content"]),
        page_content=response["page_content"],
        window=response["window"],
        metadata=response.get("metadata"),
        links=response.get("links"),
        continuation_notice=response.get("continuation_notice"),
        content_type=response.get("content_type"),
        error=response.get("error"),
    )
    _record_tool_success("get_content", output_content=response["page_content"])
    return response


@mcp.tool(
    annotations=ToolAnnotations(
        title="Batch Get Content",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def batch_get_content(
    urls: list[str],
    max_concurrency: int = 4,
    per_item_char_length: int = 8_000,
    total_char_budget: int = 120_000,
    cursor: str | None = None,
    include_metadata: bool = True,
    include_links: bool = False,
    max_links: int = 25,
    strip_selectors: str | None = None,
    ctx: Context = CurrentContext(),
) -> dict:
    """Fetch multiple URLs with structured status, budgets, and continuation cursor.

    When to use:
    - Prefer this over multiple get_content calls when you have 3+ URLs.
    - Fetch several search results under one total_char_budget.
    - Continue a partial batch by passing the returned cursor.

    When not to use:
    - If you have exactly one URL -> use get_content.
    - If you still need to discover URLs -> use web_search first.

    Args:
    - urls: URLs to fetch. Duplicates are normalized and deduplicated.
    - max_concurrency: Parallel fetch limit. Default 4, capped at 8.
    - per_item_char_length: Maximum characters per returned URL window.
    - total_char_budget: Maximum total characters returned across this page.
    - cursor: Continuation cursor from a prior partial batch response.
    - include_metadata: Include extracted page metadata in each result.
    - include_links: Include extracted links in each result.
    - max_links: Maximum links to extract per URL when include_links is true.
    - strip_selectors: Optional CSS selectors to remove before extraction.

    Returns:
    - results: per-URL structured statuses with page_content, window metadata, and optional metadata/links.
    - total_requested, total_returned, total_chars_returned.
    - has_more and cursor. If has_more is true, call again with cursor.

    This tool isolates failures per URL and keeps payloads bounded.
    """
    max_urls = _get_int_env("KINDLY_BATCH_GET_CONTENT_MAX_URLS", 30)
    bounded_urls = urls[: max(1, max_urls)]
    safe_concurrency = max(1, min(max_concurrency, 8))
    safe_item_length = max(
        500,
        min(per_item_char_length, _get_int_env("KINDLY_GET_CONTENT_MAX_CHARS", 50_000)),
    )
    safe_total_budget = max(
        2_000,
        min(
            total_char_budget,
            _get_int_env("KINDLY_BATCH_TOTAL_CHAR_BUDGET_MAX", 300_000),
        ),
    )

    emit_tool_observability_event(
        LOGGER,
        "batch_get_content",
        "request",
        urls=bounded_urls,
        url_count=len(bounded_urls),
        max_concurrency=safe_concurrency,
        per_item_char_length=safe_item_length,
        total_char_budget=safe_total_budget,
        has_cursor=bool(cursor),
        include_metadata=include_metadata,
        include_links=include_links,
        max_links=max_links,
        strip_selectors=strip_selectors,
    )

    await ctx.info(
        f"Batch fetching {len(bounded_urls)} URLs (concurrency={safe_concurrency}, budget={safe_total_budget})..."
    )
    await ctx.report_progress(
        progress=10, total=100, message=f"Fetching {len(bounded_urls)} URLs..."
    )
    output = await run_batch_fetch(
        urls=bounded_urls,
        params=BatchParams(
            max_concurrency=safe_concurrency,
            per_item_char_length=safe_item_length,
            total_char_budget=safe_total_budget,
            per_url_timeout_seconds=max(
                10.0, _resolve_tool_total_timeout_seconds() / max(len(bounded_urls), 1)
            ),
        ),
        cursor=cursor,
        fetch_options=build_fetch_options(
            include_metadata=include_metadata,
            include_links=include_links,
            max_links=max_links,
            strip_selectors=strip_selectors,
        ),
    )

    response = BatchGetContentResponse(
        results=[
            {
                "input_url": item["input_url"],
                "normalized_url": item["normalized_url"],
                "fetched_url": item["fetched_url"],
                "status": item["status"],
                "source_type": item["source_type"],
                "fetch_backend": item["fetch_backend"],
                "content_type": item.get("content_type"),
                "page_content": item["page_content"],
                "window": item["window"],
                "metadata": item.get("metadata") if include_metadata else None,
                "links": item.get("links") if include_links else None,
                "continuation_notice": item.get("continuation_notice"),
                "error": item.get("error"),
            }
            for item in output["results"]
        ],
        total_requested=output["total_requested"],
        total_returned=output["total_returned"],
        total_chars_returned=output["total_chars_returned"],
        has_more=output["has_more"],
        cursor=output["cursor"],
    ).model_dump(exclude_none=True)
    for result in response["results"]:
        result.setdefault("fetched_url", None)

    success_count = sum(1 for r in response["results"] if r["status"] == "success")
    await ctx.report_progress(
        progress=100,
        total=100,
        message=f"Done: {success_count}/{len(response['results'])} fetched",
    )
    await ctx.info(
        f"Fetched {success_count}/{len(response['results'])} in this page; has_more={response['has_more']}"
    )
    emit_tool_observability_event(
        LOGGER,
        "batch_get_content",
        "response",
        url_count=len(bounded_urls),
        success_count=success_count,
        error_count=len(response["results"]) - success_count,
        results=response["results"],
        has_more=response["has_more"],
        cursor=response.get("cursor"),
        total_requested=response.get("total_requested"),
        total_returned=response.get("total_returned"),
        total_chars_returned=response.get("total_chars_returned"),
    )
    _record_tool_success(
        "batch_get_content",
        input_url_count=len(bounded_urls),
        output_result_count=len(response["results"]),
    )
    return response


@mcp.tool(
    annotations=ToolAnnotations(
        title="Discover Links",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def discover_links(
    url: str,
    max_links: int = 100,
    include_external: bool = True,
    same_domain_only: bool = False,
    strip_selectors: str | None = None,
    ctx: Context = CurrentContext(),
) -> dict:
    """Discover outbound links from a page or sitemap without extracting article text.

    When to use:
    - You already have a URL and want to expand into nearby pages.
    - You want sitemap or page-link discovery without fetching page content.

    When not to use:
    - If you need article/body text -> use `get_content(url)`.
    - If you still need to discover the starting URL -> use `web_search(...)`.

    Args:
    - url: The page or sitemap URL to inspect.
    - max_links: Maximum links to return. Default 100.
    - include_external: Include links outside the source domain.
    - same_domain_only: Restrict results to the same domain as the source URL.
    - strip_selectors: Optional CSS selectors to remove before extraction.
    """

    await ctx.report_progress(progress=10, total=100, message="Discovering links...")
    await ctx.info(f"Discovering links from: {url[:80]}...")
    emit_tool_observability_event(
        LOGGER,
        "discover_links",
        "request",
        url=url,
        max_links=max_links,
        include_external=include_external,
        same_domain_only=same_domain_only,
        strip_selectors=strip_selectors,
    )

    output = await discover_page_links(
        url,
        max_links=max_links,
        include_external=include_external,
        same_domain_only=same_domain_only,
        strip_selectors=strip_selectors,
    )

    response = DiscoverLinksResponse(
        input_url=output["input_url"],
        normalized_url=output["normalized_url"],
        fetched_url=output.get("fetched_url"),
        source_type=output["source_type"],
        links=output.get("links", []),
        returned_links=output.get("returned_links", 0),
        has_more=output.get("has_more", False),
        metadata=output.get("metadata"),
        error=output.get("error"),
    ).model_dump(exclude_none=True)

    await ctx.report_progress(progress=100, total=100, message="Done")
    await ctx.info(
        f"Discovered {response['returned_links']} links from {response['source_type']}"
    )
    emit_tool_observability_event(
        LOGGER,
        "discover_links",
        "response",
        url=url,
        source_type=response["source_type"],
        returned_links=response["returned_links"],
        has_more=response["has_more"],
        links=response.get("links", []),
        metadata=response.get("metadata"),
        error=response.get("error"),
    )
    _record_tool_success(
        "discover_links",
        input_url_count=1,
        output_result_count=len(response.get("links", [])),
    )
    return response


@mcp.tool(
    annotations=ToolAnnotations(
        title="Gemini Search",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def gemini_search(
    query: str,
    structured_output: bool = False,
    research_goal: str | None = None,
    ctx: Context = CurrentContext(),
) -> dict:
    """Search with Gemini Google Search grounding for quick, grounded answers.

    When to use:
    - Need a quick, factual answer with Google Search grounding
    - Want citations directly from search results
    - Researching current events, facts, or technical documentation

    When not to use:
    - Need multiple web pages to compare -> use web_search instead
    - Need full page content extraction -> use web_search + get_content

    Args:
    - query: Search query for grounded answer generation
    - structured_output: If True, returns structured JSON with executive_summary,
      key_findings, sources, confidence. Default is False (plain text answer).
    - research_goal: Optional context/goal from client to guide the research.
      Helps Gemini focus the answer toward the specific need.

    Returns:
    - Plain text mode: {"query": str, "answer": str, "web_search_queries": list,
      "grounding_chunks": list, "error": str or null}
    - Structured mode: {"query": str, "structured_result": dict, ...}

    Notes:
    - Uses Gemini with Google Search grounding for real-time information
    - Provides inline citations with [N] notation
    - Requires KINDLY_GEMINI_API_KEY environment variable
    """
    emit_tool_observability_event(
        LOGGER,
        "gemini_search",
        "request",
        query=query,
        structured_output=structured_output,
        research_goal=research_goal,
    )
    import time

    start_time = time.time()
    await ctx.report_progress(
        progress=10, total=100, message="Querying Gemini with grounding..."
    )
    try:
        result = await gemini_search_with_grounding(
            query, structured_output=structured_output, research_goal=research_goal
        )
        response = result.model_dump(exclude_none=True)
        duration_seconds = time.time() - start_time

        # Record Gemini search telemetry
        grounding_queries = len(response.get("web_search_queries", []))
        grounding_chunks = len(response.get("grounding_chunks", []))
        record_gemini_search(
            grounding_queries=grounding_queries,
            grounding_chunks=grounding_chunks,
            structured_output=structured_output,
            duration_seconds=duration_seconds,
        )

        emit_tool_observability_event(
            LOGGER,
            "gemini_search",
            "response",
            query=query,
            structured_output=structured_output,
            research_goal=research_goal,
            answer=response.get("answer"),
            structured_result=response.get("structured_result"),
            web_search_queries=response.get("web_search_queries", []),
            grounding_chunks=response.get("grounding_chunks", []),
            error=response.get("error"),
        )
        _record_tool_success(
            "gemini_search",
            input_query=query,
            output_content=response.get("answer")
            if isinstance(response.get("answer"), str)
            else None,
        )
        await ctx.report_progress(progress=100, total=100, message="Done")
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
        raise


@mcp.tool(
    annotations=ToolAnnotations(
        title="Perplexity Search",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def perplexity_search(
    query: str,
    depth: str = "normal",
    research_goal: str | None = None,
    ctx: Context = CurrentContext(),
) -> dict:
    """AI-powered web search using Perplexity Sonar via Pollinations API.

    Returns SYNTHESIZED ANSWERS with source citations, NOT URL lists like web_search.
    Use for questions requiring AI analysis across multiple sources.

    ⚠️ EXPENSIVE RESOURCE: This tool is rate-limited. First call returns a steering
    message with query-writing best practices. Refine your query and retry.

    When to use:
    - Need an AI-synthesized answer with citations, not just a list of URLs
    - Questions requiring reasoning or synthesis across multiple sources
    - Research questions where you want the AI to analyze and summarize findings

    When not to use:
    - Need to browse specific URLs yourself -> use web_search instead
    - Need full page content extraction -> use web_search + get_content

    Args:
    - query: Search query string. Example: 'What are the latest React 19 features?'
    - depth: Search depth: 'normal' (Perplexity Sonar, balanced) or
      'deep' (Perplexity Sonar Reasoning, complex reasoning). Default: 'normal'.
    - research_goal: Optional context/goal from client to guide the research.
      Helps Perplexity focus the answer toward the specific need.

    Returns:
    - {"query": str, "answer": str, "sources": list[str], "model": str, "error": str|null}

    Notes:
    - Uses Perplexity Sonar models via Pollinations API
    - Returns AI-synthesized text answer with source citations
    - Requires POLLINATIONS_API_KEY environment variable
    """
    from .search.pollinations import get_pollinations_client

    client = get_pollinations_client()
    emit_tool_observability_event(
        LOGGER,
        "perplexity_search",
        "request",
        query=query,
        depth=depth,
        research_goal=research_goal,
    )
    import time

    start_time = time.time()

    await ctx.report_progress(
        progress=10, total=100, message="Querying Perplexity Sonar..."
    )

    try:
        result = await client.web_search(query, depth, research_goal=research_goal)
        response = {
            "query": result["query"],
            "answer": result["answer"],
            "sources": result["sources"],
            "model": result["model"],
            "error": None,
        }
        duration_seconds = time.time() - start_time

        # Record Perplexity search telemetry
        source_count = len(response["sources"])
        model = response["model"] or "sonar"
        record_perplexity_search(
            depth=depth,
            source_count=source_count,
            model=model,
            duration_seconds=duration_seconds,
        )

        emit_tool_observability_event(
            LOGGER,
            "perplexity_search",
            "response",
            query=query,
            depth=depth,
            research_goal=research_goal,
            answer=response["answer"],
            sources=response["sources"],
            model=response["model"],
            error=None,
        )
        _record_tool_success(
            "perplexity_search",
            input_query=query,
            output_content=response["answer"],
        )
        await ctx.report_progress(progress=100, total=100, message="Done")
        return response
    except ValueError as e:
        duration_seconds = time.time() - start_time
        record_perplexity_search(
            depth=depth,
            source_count=0,
            model="sonar",
            duration_seconds=duration_seconds,
        )
        _record_tool_failure("perplexity_search")
        return format_tool_error(e, provider="perplexity")
    except httpx.HTTPError as e:
        duration_seconds = time.time() - start_time
        record_perplexity_search(
            depth=depth,
            source_count=0,
            model="sonar",
            duration_seconds=duration_seconds,
        )
        LOGGER.warning(f"Perplexity search failed: {e}")
        emit_tool_observability_event(
            LOGGER,
            "perplexity_search",
            "error",
            level=logging.WARNING,
            query=query,
            depth=depth,
            research_goal=research_goal,
            error_type=type(e).__name__,
            error_message=str(e),
        )
        _record_tool_failure("perplexity_search")
        return format_tool_error(e, provider="perplexity")
    except Exception as e:
        LOGGER.warning(f"Perplexity search unexpected error: {e}")
        emit_tool_observability_event(
            LOGGER,
            "perplexity_search",
            "error",
            level=logging.WARNING,
            query=query,
            depth=depth,
            research_goal=research_goal,
            error_type=type(e).__name__,
            error_message=str(e),
        )
        _record_tool_failure("perplexity_search")
        return format_tool_error(e, provider="perplexity")


@mcp.tool(
    annotations=ToolAnnotations(
        title="YouTube Transcript",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def youtube_transcript(
    video_id_or_url: str,
    language: str | None = None,
    translate_to: str | None = None,
    format: str = "text",
) -> dict:
    """Retrieve transcript/captions from a YouTube video.

    Extracts transcript data from YouTube videos using the youtube-transcript-api
    library. Supports multiple URL formats and direct video IDs.

    When to use:
    - Need to analyze or summarize video content
    - Extract spoken content from YouTube videos for AI processing
    - Get timestamped transcript for citation/reference
    - Use after youtube_search has returned a video URL or video ID.

    When not to use:
    - Video has no captions/transcripts available
    - Video is private, deleted, or age-restricted

    Args:
    - video_id_or_url: YouTube video URL or bare video ID (11 chars).
      Supported formats:
      - https://www.youtube.com/watch?v=VIDEO_ID
      - https://youtu.be/VIDEO_ID
      - https://www.youtube.com/embed/VIDEO_ID
      - https://www.youtube.com/shorts/VIDEO_ID
      - https://www.youtube.com/live/VIDEO_ID
      - Bare VIDEO_ID (11 chars, alphanumeric + underscore/dash)
    - language: Preferred language code (e.g., "en", "es"). Defaults to "en".
    - translate_to: Target language for translation (e.g., "de", "fr").
    - format: Output format: "text" (plain text), "timestamped" ([MM:SS] lines),
      or "json" (raw segments). Default: "text".

    Returns:
    - YouTubeTranscriptResponse with video_id, transcript_text, language, duration, etc.
    - If transcript fetch fails, response includes `error` field with message.

    Notes:
    - Recommended chain: youtube_search(query) -> youtube_transcript(video_id_or_url).
    - Transcripts are auto-generated or manually provided by video creators.
    - Some videos have disabled transcripts.
    - Cloud IPs (AWS/GCP/Azure) may be blocked; use KINDLY_YOUTUBE_TRANSCRIPT_PROXY_URL.
    """

    timeout_seconds = settings.youtube_transcript_timeout_seconds
    max_chars = settings.youtube_transcript_max_chars

    try:
        # Parse URL/ID
        target = parse_youtube_url(video_id_or_url)
        video_id = target.video_id
        canonical_url = target.canonical_url

        # Fetch transcript with timeout
        segments = await asyncio.wait_for(
            asyncio.to_thread(
                fetch_transcript_data,
                video_id,
                language=language,
                translate_to=translate_to,
            ),
            timeout=timeout_seconds,
        )

        # Determine language and translation status
        actual_language = language or "en"
        is_translated = bool(translate_to)

        # Format output
        if format == "json":
            transcript_text = ""
        elif format == "timestamped":
            transcript_text = format_transcript_timestamped(segments)
        else:
            transcript_text = format_transcript_text(segments)

        # Truncate if needed
        if len(transcript_text) > max_chars:
            transcript_text = transcript_text[:max_chars].rstrip() + "…"
            LOGGER.info(
                f"Truncated transcript to {max_chars} chars for video {video_id}"
            )

        duration_seconds = calculate_total_duration(segments)

        # Record YouTube transcript telemetry
        record_youtube_transcript(
            format=format,
            language=actual_language,
            is_translated=is_translated,
            duration_seconds=int(duration_seconds),
        )

        return YouTubeTranscriptResponse(
            video_id=video_id,
            video_url=canonical_url,
            title=None,  # Title requires separate YouTube Data API call
            transcript_text=transcript_text,
            language=actual_language,
            is_translated=is_translated,
            duration_seconds=duration_seconds,
            transcript_segments=segments if format == "json" else None,
            error=None,
        ).model_dump(exclude_none=True)

    except asyncio.TimeoutError:
        record_youtube_transcript(
            format=format,
            language=language or "en",
            is_translated=bool(translate_to),
            duration_seconds=None,
        )
        error_msg = f"Transcript fetch timed out after {timeout_seconds}s"
        return {
            "video_id": "",
            "video_url": video_id_or_url,
            "transcript_text": "",
            "language": language or "en",
            "error": error_msg,
            "isError": True,
            "error_type": "network",
            "action": "The request took too long. Try again or check network connectivity.",
        }

    except YouTubeError as e:
        record_youtube_transcript(
            format=format,
            language=language or "en",
            is_translated=bool(translate_to),
            duration_seconds=None,
        )
        return {
            "video_id": "",
            "video_url": video_id_or_url,
            "transcript_text": "",
            "language": language or "en",
            "error": str(e),
            "isError": True,
            "error_type": "content",
            "action": "Transcripts may be disabled or unavailable for this video.",
        }

    except Exception as e:
        record_youtube_transcript(
            format=format,
            language=language or "en",
            is_translated=bool(translate_to),
            duration_seconds=None,
        )
        LOGGER.warning(f"YouTube transcript unexpected error: {e}")
        structured = classify_error(e, provider="youtube")
        return {
            "video_id": "",
            "video_url": video_id_or_url,
            "transcript_text": "",
            "language": language or "en",
            "error": structured.error,
            "isError": True,
            "error_type": structured.error_type,
            "action": structured.action,
        }


@mcp.tool(
    annotations=ToolAnnotations(
        title="YouTube Search",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def youtube_search(
    query: str,
    num_results: int = 5,
) -> dict:
    """Search YouTube videos via SearXNG YouTube engine.

    Searches for YouTube videos using the SearXNG metasearch engine's
    built-in YouTube engine filter. Returns lightweight results with
    video metadata.

    When to use:
    - Find relevant YouTube videos on a topic
    - Discover video content before extracting transcripts
    - Search for tutorials, lectures, or presentations

    When not to use:
    - Need to read full video content -> use youtube_transcript instead
    - Need general web search -> use web_search instead

    Args:
    - query: Search query string.
    - num_results: Number of results to return (1-20, default 5).

    Returns:
    - YouTubeSearchResponse with query and list of WebSearchResult objects.
    - Each result has title, link (YouTube URL), snippet, and resource_type="youtube".

    Notes:
    - Requires SEARXNG_BASE_URL to be configured.
    - Uses SearXNG's YouTube engine for video-specific search.
    - Results are suitable for follow-up with youtube_transcript tool.
    """

    if num_results < 1:
        num_results = 5
    num_results = min(num_results, 20)
    import time

    start_time = time.time()

    try:
        results = await search_youtube_videos(query, num_results=num_results)
        duration_seconds = time.time() - start_time

        # Record YouTube search telemetry
        record_youtube_search(
            num_results=len(results),
            duration_seconds=duration_seconds,
        )

        return YouTubeSearchResponse(
            query=query,
            results=results,
            total_results=len(results),
        ).model_dump(exclude_none=True)

    except YouTubeSearchError as e:
        duration_seconds = time.time() - start_time
        record_youtube_search(
            num_results=0,
            duration_seconds=duration_seconds,
        )
        return {
            "query": query,
            "results": [],
            "error": str(e),
            "isError": True,
            "error_type": "network",
            "action": "YouTube search via SearXNG failed. Check SEARXNG_BASE_URL configuration.",
        }

    except Exception as e:
        duration_seconds = time.time() - start_time
        record_youtube_search(
            num_results=0,
            duration_seconds=duration_seconds,
        )
        LOGGER.warning(f"YouTube search unexpected error: {e}")
        return format_tool_error(e, provider="youtube")


@mcp.tool(
    annotations=ToolAnnotations(
        title="Academic Search",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
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
) -> dict:
    """Search academic papers across 6 scholarly sources.

    Finds research papers, preprints, and citations across major academic
    sources. Results are deduplicated across providers and normalized to a
    common schema.

    When to use:
    - Find research papers on a topic (machine learning, physics, medicine, etc.)
    - Check citation counts and find influential papers
    - Discover open-access PDFs for a research area
    - Filter papers by year, venue, or field of study
    - Biomedical/clinical literature search (PubMed)

    When not to use:
    - Need general web search -> use web_search instead
    - Need full page content extraction -> use get_content on paper URLs

    Args:
    - query: Search query for academic papers. Prefer specific keywords and
      paper titles. Example: "attention is all you need" or
      "transformer neural network architecture"
    - limit: Maximum results to return. Default 5; range 1-20.
    - sources: Optional list of sources to search. Available: "semanticscholar",
      "arxiv", "openalex", "crossref", "pubmed", "core".
      Default: arxiv + semanticscholar (both free-ish).
    - year_from: Filter to papers published in or after this year. Example: 2020
    - year_to: Filter to papers published in or before this year. Example: 2024
    - fields_of_study: Filter by field. Semantic Scholar values:
      Computer Science, Medicine, Physics, Mathematics, Statistics, etc.
      ArXiv/OpenAlex also support field categories.
    - venue: Filter by publication venue (conference/journal name).
      Example: "NeurIPS", "ICML", "Nature". Only Semantic Scholar supports this.
    - open_access_only: If True, only return papers with available open-access PDFs.
    - sort: Result ordering: "relevance" (default), "citations", or "date".

    Returns:
    - AcademicSearchResponse with query, results list, total_results,
      sources_used, and optional warnings.

    Notes:
    - Semantic Scholar: 214M+ papers, rich metadata (citations, abstracts).
      Optional KINDLY_S2_API_KEY for 100 RPS vs shared 1 RPS.
    - ArXiv: 2.5M+ CS/Physics/Math preprints. No auth required.
    - OpenAlex: 250M+ works, comprehensive coverage. Polite pool with email.
    - CrossRef: DOI enrichment, citation counts, bibliographic metadata.
    - PubMed: 35M+ biomedical citations (MEDLINE). Optional API key for 10 RPS.
    - CORE: Open access full-text aggregation. Requires CORE_API_KEY.
    - Results deduplicated by DOI, ArXiv ID, PubMed ID, or title match.
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

    filter_params = {
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
            LOGGER.debug(f"Exact query cache hit for academic search: {query[:100]}")
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
            return exact_cached
    except Exception as e:
        LOGGER.warning(f"Exact query cache lookup failed for academic search: {e}")

    if settings.semantic_cache_enabled:
        try:
            cache_store = _get_cache_store()
            cached = await get_semantic_cache(
                cache_store,
                query,
                min_score=settings.semantic_cache_min_score,
                provider_key=cache_providers_key,
            )
            if cached:
                LOGGER.debug(f"Semantic cache hit for academic search: {query[:100]}")

                answer_json = cached.get("answer_json") or "{}"
                parsed = json.loads(answer_json)
                parsed["query"] = query
                emit_tool_observability_event(
                    LOGGER,
                    "academic_search",
                    "response",
                    cache_hit="semantic",
                    query=query,
                    result_count=len(parsed.get("results", [])),
                    sources_used=parsed.get("sources_used", []),
                )
                _record_tool_success(
                    "academic_search",
                    input_query=query,
                    output_result_count=len(parsed.get("results", [])),
                )
                return parsed
        except Exception as e:
            LOGGER.warning(f"Semantic cache lookup failed for academic search: {e}")

    await ctx.report_progress(
        progress=20, total=100, message="Searching academic sources..."
    )

    async def _execute_academic_search() -> dict:
        from .search.academic_search_orchestrator import run_academic_search

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
            LOGGER.debug(f"Stored exact query cache for academic search: {query[:100]}")
        except Exception as e:
            LOGGER.warning(f"Exact query cache write failed for academic search: {e}")

        if settings.semantic_cache_enabled:
            try:
                cache_store = _get_cache_store()
                content_type = classify_content_type(query)

                async def _safe_academic_cache_write() -> None:
                    try:
                        await set_semantic_cache(
                            cache_store,
                            query,
                            response,
                            content_type,
                            provider_key=cache_providers_key,
                        )
                    except Exception as e:
                        LOGGER.warning(
                            "Background academic semantic cache write failed: %s", e
                        )

                asyncio.create_task(_safe_academic_cache_write())
                LOGGER.debug(
                    f"Scheduled semantic cache write for academic search: {query[:100]}"
                )
            except Exception as e:
                LOGGER.warning(
                    f"Semantic cache write scheduling failed for academic search: {e}"
                )

        return response

    try:
        flight_key = SingleFlight.make_key(
            normalized_query, limit, sources_key, filter_key
        )
        response = await _academic_search_flight.do(
            flight_key, _execute_academic_search
        )

        _record_tool_success(
            "academic_search",
            input_query=query,
            output_result_count=len(response.get("results", [])),
        )
        await ctx.report_progress(progress=100, total=100, message="Done")
        await ctx.info(
            f"Found {len(response.get('results', []))} academic results from {response.get('sources_used', [])}"
        )
        return response
    except Exception as e:
        LOGGER.warning(f"Academic search failed: {e}")
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
        return format_tool_error(e, provider="academic_search")


# ============ RESOURCES ============


@mcp.resource("status://providers")
def get_providers_status() -> str:
    """Which search providers are configured and their health state."""
    from .search.provider_health import get_provider_health

    lines = [
        "# Search Provider Status",
        "",
        f"**SearXNG** (Primary): {'✓ Configured' if os.environ.get('SEARXNG_BASE_URL') else '✗ Not configured'}",
        f"**Tavily**: {'✓ Configured' if os.environ.get('TAVILY_API_KEY') else '✗ Not configured'}",
        f"**Brave**: {'✓ Configured' if os.environ.get('BRAVE_API_KEY') else '✗ Not configured'}",
        f"**Jina**: {'✓ Configured' if os.environ.get('JINA_API_KEY') else '✗ Not configured'}",
        f"**Voyage Reranker**: {'✓ Configured' if settings.voyage_api_key else '✗ Not configured'}",
        f"**Composio LLM Search**: {'✓ Configured' if os.environ.get('COMPOSIO_API_KEY') and os.environ.get('KINDLY_COMPOSIO_USER_ID') else '✗ Not configured'}",
        "",
        "## AI Search",
        f"**Gemini**: {'✓ Configured' if settings.gemini_api_key else '✗ Not configured'}",
        f"**Perplexity (Pollinations)**: {'✓ Configured' if os.environ.get('POLLINATIONS_API_KEY') else '✗ Not configured'}",
        "",
        "## Academic Search",
        f"**Semantic Scholar**: ✓ Always available (API key optional: {'set' if os.environ.get('KINDLY_S2_API_KEY', '').strip() else 'not set — shared rate limit'})",
        "**ArXiv**: ✓ Always available (no auth required)",
        "",
        "## Other",
        f"**GitHub Token**: {'✓ Configured' if os.environ.get('GITHUB_TOKEN') else '✗ Not configured'}",
        "",
        "## Provider Health",
    ]

    tracker = get_provider_health()
    for state in tracker.all_states():
        if state["cooldown_remaining_s"] > 0:
            lines.append(
                f"- **{state['provider']}**: ⚠️ IN COOLDOWN ({state['cooldown_remaining_s']}s remaining) — "
                f"{state['consecutive_failures']} consecutive failures"
            )
        elif state["total_failures"] > 0:
            lines.append(
                f"- **{state['provider']}**: ✓ healthy — "
                f"{state['total_successes']} successes, {state['total_failures']} failures"
            )

    if not tracker.all_states():
        lines.append("- No providers have been called yet.")

    return "\n".join(lines)


@mcp.resource("status://features")
def get_features_status() -> str:
    """Server feature flags status."""
    lines = [
        "# Feature Status",
        "",
        f"**Semantic Cache**: {'✓ Enabled' if settings.semantic_cache_enabled else '✗ Disabled'}",
        f"**Query Rewrite**: {'✓ Enabled' if settings.query_rewrite_enabled else '✗ Disabled'}",
        f"**Reranking**: {'✓ Enabled' if settings.reranking_enabled else '✗ Disabled'}",
        "",
        "## Cache Settings",
        f"LanceDB Path: {settings.lancedb_dir}",
        "",
        "## Timeouts",
        f"Tool Timeout: {os.environ.get('KINDLY_TOOL_TOTAL_TIMEOUT_SECONDS', '120')}s",
        f"YouTube Transcript Timeout: {settings.youtube_transcript_timeout_seconds}s",
    ]
    return "\n".join(lines)


@mcp.resource("docs://workflow")
def get_workflow_doc() -> str:
    """Recommended workflow for using web search tools."""
    return """# Web Search Workflow

## Routing

1. Start with web_search for URL discovery. rewrite=true is standard.
2. Use rewrite=false only for exact errors, URLs, versions, hashes, UUIDs, and quoted literals.
3. Use get_content for one known URL.
4. Use batch_get_content for 3+ URLs. Continue with cursor when has_more=true.
5. Use discover_links when you already have a URL and want outbound links or sitemap targets.
6. Use gemini_search for quick grounded synthesis.
7. Use perplexity_search only after the query is narrowed to one topic.
8. Use academic_search for scholarly papers with year/venue/field filters.
9. Use youtube_search before youtube_transcript.
10. Use composio_similarlinks to expand from a known good URL.

## Discovery -> Extraction -> Synthesis

### Step 1: Search
web_search(query="your specific question", research_goal="why you need it", num_results=5, rewrite=True)
Returns lightweight results: title, link, snippet, provider_count.

### Step 2: Extract
get_content(url="https://selected-url")
Returns a bounded content window. If window.has_more is true, call again with char_offset=window.next_offset.

For 3+ URLs:
batch_get_content(urls=[...], total_char_budget=120000)
If has_more is true, call again with cursor.

### Step 3: Synthesize
gemini_search(query="focused question", research_goal="specific synthesis need")
Use perplexity_search only when a refined single-topic query needs deeper synthesis.

| Tool | Purpose |
|------|---------|
| web_search | Discover URLs |
| get_content | Read specific URL |
| batch_get_content | Read 3+ URLs with budget/cursor |
| discover_links | Expand a known URL into outbound links |
| gemini_search | Quick grounded answers |
| perplexity_search | Deep reasoning synthesis |
| academic_search | Find scholarly papers (S2 + ArXiv) |
| youtube_search | Find videos |
| youtube_transcript | Extract video captions |
| composio_similarlinks | Find related URLs from a known good URL |
| quick_web_search | Composio/Exa-backed synthesized answer with citations |

## Tips
- Search exact error messages in quotes with rewrite=false
- Prefer official docs and GitHub issues for implementation work
- Use provider_count as a confidence hint, not proof
- Use num_results=3-7 for normal discovery; use 1 for quick existence checks
"""


# ============ PROMPTS ============

_SEARCH_TOOL_ROUTING = """Tool selection rules for this server:

| Your task | Use this tool | Why |
|---|---|---|
| Find URLs about a topic | `web_search` | Lightweight results, multi-provider merge, provider_count signal |
| Quick factual answer with citations | `gemini_search` | Google-grounding, [N] citations, fast |
| Deep reasoning across many sources | `perplexity_search` | AI-synthesized, expensive, refine query first |
| Scholarly papers with filters | `academic_search` | 6 sources (S2, ArXiv, PubMed...), field/venue/year filters |
| Read one known URL | `get_content` | 7-stage resolution (GitHub→StackExchange→Wikipedia→arXiv→HTTP→browser) |
| Read 3+ URLs with a budget | `batch_get_content` | Parallel fetch, total_char_budget, cursor continuation |
| Expand a known URL into outgoing links | `discover_links` | Page/sitemap link discovery without body extraction |
| Find videos | `youtube_search` | SearXNG YouTube engine |
| Extract video speech | `youtube_transcript` | Timestamped or plain text, translation |
| Quick synthesized answer | `quick_web_search` | Exa-backed, lighter than perplexity |
| Expand from a known URL | `composio_similarlinks` | Neural similarity, filter by domain |

Query formulation:
- `rewrite=true` (default): Mistral expands your query for broader coverage. Use for normal discovery.
- `rewrite=false`: Exact literal search. Use for error messages, versions, hashes, UUIDs, quoted strings.
- `num_results=5`: Standard. Use 3 for fast checks, 7 for broad coverage, max 10.
- `providers`: Standard providers (SearXNG, DDG, Gemini) fire automatically. Request tavily/brave/jina explicitly.
- Academic: use `year_from`/`year_to`, `venue` ("NeurIPS"), `fields_of_study`, `open_access_only`.

Depth strategy:
- quick: `gemini_search` or `quick_web_search`. Skip content extraction unless needed.
- medium: `web_search` (5 results) → `batch_get_content` on best 2–3 → `gemini_search` for synthesis.
- deep: `web_search` (7 results, rewrite=true) → `batch_get_content` on top 5 → `perplexity_search` on refined query → `academic_search` if scholarly sources needed."""


_RESULT_EVALUATION_RULES = """Quality signals to check after every search:

1. provider_count — How many configured providers returned this URL.
   - 2+: stronger signal. provider_count=1 may still be good but verify.
   - 0 or missing: single-source result, treat with lower confidence.

2. Snippet quality — Read snippets before deciding to fetch.
   - Specific facts, code, dates, version numbers: high signal.
   - Generic marketing text: low signal.
   - Domain hints: github.com→likely issue/PR, stackoverflow.com→Q&A, docs.*→official docs.

3. Domain authority (heuristic, not absolute):
   - .gov, .edu, official docs sites: generally trustworthy.
   - github.com issues/PRs: high signal for debugging.
   - stackoverflow.com / stackexchange: high signal for how-to questions.
   - Medium, dev.to, personal blogs: verify against official sources.

Decision rules after evaluating results:
- 3+ results look promising → `batch_get_content(urls=[...])` with appropriate total_char_budget.
- Only 1–2 look good → `get_content(url=...)` on each; check `window.has_more`.
- Results seem off-topic → refine query: different keywords, add domain terms, try `gemini_search` for quick reorientation.
- Results are sparse (< 3 returned) → broaden: remove specific terms, try rewrite=true if it was false.
- Results exist but snippets are thin → fetch the most promising URL before deciding.
- Need deep analysis → refine to ONE focused question, then `perplexity_search`.

Pagination awareness:
- `get_content`: check `window.has_more`. If true, call again with `char_offset=window.next_offset`.
- `batch_get_content`: check `has_more` and `cursor`. If true, call again with `cursor`.
- Never assume you have the full page without checking these signals."""


_GAP_ANALYSIS_RULES = """After initial research, systematically evaluate what's missing before continuing or stopping.

Gap identification:
1. Factual gaps: What specific claims, numbers, dates, or API details are unverified?
2. Source gaps: Did you only find one type of source (e.g., only blog posts, no official docs)?
3. Perspective gaps: Did you only get one viewpoint? (e.g., only author docs, no community critique)
4. Recency gaps: Are your sources current? Check dates in snippets or fetched content.
5. Depth gaps: Did you hit `has_more=true` on any fetched page? The full content may hold answers.

Query decomposition for follow-up rounds:
- Aspect decomposition: Break topic into sub-facets. "How does X work?" → "X architecture", "X performance", "X security".
- Perspective decomposition: Same question from different angles. "X tutorial" + "X pitfalls" + "X vs Y comparison".
- Refinement: Narrow with domain terms, version numbers, or date ranges found in initial results.
- Counter-query: If results lean one way, explicitly search for opposing views or known issues.

Source triangulation:
- One source = interesting. Two independent sources agreeing = likely true. Three+ = well-established.
- If a claim only appears on one domain, flag it as unverified.
- Cross-check: community sources (Reddit, HN) for real-world experience vs official docs for API accuracy.

Termination criteria — stop when:
- Three independent sources confirm the same finding.
- Two consecutive rounds produce no new information.
- You've checked: official docs + GitHub issues + one community source (minimum coverage).
- `provider_count` ≥ 3 on your key source URLs.
- Depth budget exhausted: quick → medium → deep completed and gaps remain.

Breadth decay: each iteration narrower than the last.
- Round 1: Broad discovery (`web_search`, num_results=5–7, rewrite=true).
- Round 2: Targeted follow-up (2–3 refined queries, num_results=3, specific providers if helpful).
- Round 3: Pinpoint verification (1–2 precise queries, rewrite=false for exact terms).
- Use `composio_similarlinks` on the best URL from round 1 to discover adjacent pages.
- If video content would help: `youtube_search` → `youtube_transcript` on the most relevant video."""


@mcp.prompt(
    name="plan_web_research",
    description="Plan your research approach: choose the right search tool, formulate effective queries, set depth strategy. Use BEFORE calling any search tool.",
    tags={"research", "planning"},
)
def plan_web_research_prompt(question: str, depth: str = "medium") -> list[Message]:
    return [
        Message(
            _SEARCH_TOOL_ROUTING,
            role="user",
        ),
        Message(
            f"Research question: {question}\n"
            f"Preferred depth: {depth}\n\n"
            "Plan your approach: which tool(s) will you use, what query parameters, "
            "and what sequence of steps? State your plan before executing.",
        ),
    ]


@mcp.prompt(
    name="evaluate_web_results",
    description="Assess search result quality and decide next action: fetch content, refine query, or escalate. Use AFTER web_search or academic_search returns.",
    tags={"research", "evaluation"},
)
def evaluate_web_results_prompt(goal: str) -> list[Message]:
    return [
        Message(
            _RESULT_EVALUATION_RULES,
            role="user",
        ),
        Message(
            f"Research goal: {goal}\n\n"
            "Review the search results you just received. Evaluate their quality using the signals above. "
            "State your assessment and decide your next action. If results are insufficient, "
            "explain why and what you'll try instead.",
        ),
    ]


@mcp.prompt(
    name="research_gap_analysis",
    description="Identify what's missing after initial research, decompose remaining questions, and plan the next iteration. Use AFTER evaluating search results.",
    tags={"research", "iteration"},
)
def research_gap_analysis_prompt(goal: str, sources_found: str = "") -> list[Message]:
    return [
        Message(
            _GAP_ANALYSIS_RULES,
            role="user",
        ),
        Message(
            f"Research goal: {goal}\n"
            + (f"Sources examined so far: {sources_found}\n" if sources_found else "")
            + "\nReview what you've found against the original goal. "
            "Identify specific gaps, plan the next round of queries (with tool choices and parameters), "
            "and state your termination criteria. If gaps remain, proceed with the next iteration. "
            "If you've met the termination criteria, state that research is complete and why.",
        ),
    ]


@mcp.prompt(
    name="suggest_tool",
    description="Given a task description, recommend the best search tool(s) and parameters. Use when unsure which of the available tools fits your need.",
    tags={"discovery"},
)
def suggest_tool_prompt(task: str) -> list[Message]:
    return [
        Message(
            _SEARCH_TOOL_ROUTING,
            role="user",
        ),
        Message(
            f"Task: {task}\n\n"
            "Which tool(s) should I use? Recommend specific tool names and key parameters. "
            "If multiple tools should be used in sequence, describe the full chain.",
        ),
    ]
