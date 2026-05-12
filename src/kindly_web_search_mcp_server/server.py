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
)
from opentelemetry import trace
init_telemetry(service_name="web-search-mcp")

import argparse
import asyncio
import httpx
import logging
import os
import sys
from typing import Literal

from fastmcp import FastMCP
from fastmcp.dependencies import CurrentContext  # For context injection
from fastmcp.server.context import Context  # Context type
from mcp.types import ToolAnnotations  # For tool annotations

from .models import (
    BatchGetContentResponse,
    GetContentResponse,
    YouTubeTranscriptResponse,
    YouTubeSearchResponse,
)
from .errors import classify_error, format_tool_error
from .content.batch_orchestrator import BatchParams, run_batch_fetch
from .content.fetch_pipeline import fetch_content_artifact
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
from .settings import settings
from .utils.diagnostics import (
    Diagnostics,
    diagnostics_enabled,
    mask_env_values,
    new_request_id,
)
from .utils.logging import configure_logging
from .utils.observability import (
    emit_observability_event,
    preview_text,
)
from .utils.singleflight import SingleFlight

configure_logging()
LOGGER = logging.getLogger(__name__)

# Singleton cache store (lazy init)
_CACHE_STORE: SemanticCacheStore | None = None

# SingleFlight for request coalescing
_search_flight = SingleFlight()


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
        output_content_length=len(output_content) if output_content is not None else None,
        output_transcript_length=len(output_transcript) if output_transcript is not None else None,
    )


def _record_tool_failure(tool_name: str) -> None:
    record_mcp_tool_call(tool_name, success=False)

mcp = FastMCP(
    "kindly-web-search",
    instructions=(
        "Tool routing: use web_search first for normal web discovery and keep rewrite=true by default. "
        "Use rewrite=false only for exact literals such as stack traces, quoted errors, URLs, versions, hashes, and UUIDs. "
        "Use get_content for one known URL; use batch_get_content for 3 or more URLs and follow has_more/cursor or window.next_offset. "
        "Use gemini_search for quick grounded synthesis; use perplexity_search only after refining a single-topic query. "
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

# Add Gemini advisory middleware (non-blocking, informational)
from .middleware import create_gemini_advisory_middleware
mcp.add_middleware(create_gemini_advisory_middleware())

# Add query quality middleware for web_search (non-blocking, tips on every call)
from .middleware import create_query_quality_middleware, create_result_guidance_middleware
mcp.add_middleware(create_query_quality_middleware())
mcp.add_middleware(create_result_guidance_middleware())
register_composio_tools(mcp)

Transport = Literal["stdio", "sse", "streamable-http"]


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-web-search",
        description="MCP server: Multi-provider web search (SearXNG/Tavily/Brave/Jina) with RRF merge.",
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
    resolved_port_raw = str(port) if port is not None else os.environ.get("FASTMCP_PORT", "8000")
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
        and os.environ.get("MCP_ALLOW_TTY_STDIO", "").strip().lower() not in ("1", "true", "yes")
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
    """Remove legacy page bodies from cached search results and ensure query is present."""
    normalized: dict[str, object] = dict(response)
    normalized["query"] = query

    raw_results = normalized.get("results", [])
    if isinstance(raw_results, list):
        cleaned_results: list[dict[str, object]] = []
        for item in raw_results:
            if isinstance(item, dict):
                cleaned_item = dict(item)
                cleaned_item.pop("page_content", None)
                cleaned_results.append(cleaned_item)
        normalized["results"] = cleaned_results

    return normalized


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
        },
    ) as root_span:
        # Report progress
        await ctx.info(f"Searching: {query[:80]}...")

        # 1. Exact query cache lookup (fastest, deterministic)
        normalized_query = normalize_query(query)
        providers_key = provider_cache_key(providers)
        emit_observability_event(
            LOGGER,
            "tool.web_search.request",
            tool_name="web_search",
            query=query,
            normalized_query=normalized_query,
            research_goal=research_goal,
            num_results=num_results,
            rewrite_enabled=rewrite,
            providers_requested=providers or [],
            providers_key=providers_key,
        )
        try:
            exact_cache = get_query_cache()
            exact_cached = exact_cache.lookup(
                normalized_query=normalized_query,
                num_results=num_results,
                rewrite_enabled=rewrite,
                search_mode="balanced",  # Current default mode
                providers_key=providers_key,
            )
            if exact_cached:
                LOGGER.debug(f"Exact query cache hit for: {query[:100]}")
                root_span.set_attribute("cache.hit", "exact")
                root_span.set_attribute("search.num_results_returned", len(exact_cached.get("results", [])))
                exact_response = _normalize_lightweight_search_response(exact_cached, query=query)
                emit_observability_event(
                    LOGGER,
                    "tool.web_search.response",
                    tool_name="web_search",
                    cache_hit="exact",
                    query=query,
                    normalized_query=normalized_query,
                    result_count=len(exact_response.get("results", [])),
                    providers_used=exact_response.get("providers_used", []),
                    results=exact_response.get("results", []),
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
                    provider_key=providers_key,
                )
                if cached:
                    LOGGER.debug(f"Cache hit for query: {query[:100]}")
                    cached_response = cached.get("answer_json")
                    if cached_response:
                        import json
                        parsed = json.loads(cached_response)
                        root_span.set_attribute("cache.hit", "semantic")
                        root_span.set_attribute("search.num_results_returned", len(parsed.get("results", [])))
                        semantic_response = _normalize_lightweight_search_response(parsed, query=query)
                        emit_observability_event(
                            LOGGER,
                            "tool.web_search.response",
                            tool_name="web_search",
                            cache_hit="semantic",
                            query=query,
                            normalized_query=normalized_query,
                            result_count=len(semantic_response.get("results", [])),
                            providers_used=semantic_response.get("providers_used", []),
                            results=semantic_response.get("results", []),
                        )
                        _record_tool_success(
                            "web_search",
                            input_query=query,
                            output_result_count=len(semantic_response.get("results", [])),
                        )
                        return semantic_response
            except Exception as e:
                LOGGER.warning(f"Cache lookup failed: {e}")

        root_span.set_attribute("cache.hit", "miss")

        diag_enabled = diagnostics_enabled()

        # SingleFlight: coalesce identical concurrent searches into one execution
        flight_key = SingleFlight.make_key(normalized_query, num_results, rewrite, providers_key)

        async def _execute_search() -> dict:
            parent_request_id = new_request_id() if diag_enabled else ""
            parent_diag = Diagnostics(parent_request_id, diag_enabled, stream=sys.stderr)
            if diag_enabled:
                env_snapshot = {
                    "SEARXNG_BASE_URL": os.environ.get("SEARXNG_BASE_URL", ""),
                    "TAVILY_API_KEY": os.environ.get("TAVILY_API_KEY", ""),
                    "BRAVE_API_KEY": os.environ.get("BRAVE_API_KEY", ""),
                    "JINA_API_KEY": os.environ.get("JINA_API_KEY", ""),
                    "COMPOSIO_API_KEY": os.environ.get("COMPOSIO_API_KEY", ""),
                    "GITHUB_TOKEN": os.environ.get("GITHUB_TOKEN", ""),
                    "KINDLY_TOOL_TOTAL_TIMEOUT_SECONDS": os.environ.get("KINDLY_TOOL_TOTAL_TIMEOUT_SECONDS", ""),
                    "KINDLY_TOOL_TOTAL_TIMEOUT_MAX_SECONDS": os.environ.get(
                        "KINDLY_TOOL_TOTAL_TIMEOUT_MAX_SECONDS", ""
                    ),
                    "KINDLY_WEB_SEARCH_MAX_CONCURRENCY": os.environ.get("KINDLY_WEB_SEARCH_MAX_CONCURRENCY", ""),
                }
                parent_diag.emit(
                    "web_search.start",
                    "Starting web search",
                    {
                        "query": query,
                        "num_results": num_results,
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
            )
            if not response_model.results:
                return response_model.model_dump(exclude_none=True)

            _response = _normalize_lightweight_search_response(
                response_model.model_dump(exclude_none=True),
                query=query,
            )

            # Cache write: exact query cache
            try:
                exact_cache = get_query_cache()
                exact_cache.store(
                    normalized_query=normalized_query,
                    num_results=num_results,
                    rewrite_enabled=rewrite,
                    response=_response,
                    search_mode="balanced",
                    providers_key=providers_key,
                )
                LOGGER.debug(f"Stored exact query cache for: {query[:100]}")
            except Exception as e:
                LOGGER.warning(f"Exact query cache write failed: {e}")

            # Cache write: semantic cache
            if settings.semantic_cache_enabled:
                try:
                    cache_store = _get_cache_store()
                    content_type = classify_content_type(query)
                    await set_semantic_cache(
                        cache_store,
                        query,
                        _response,
                        content_type,
                        provider_key=providers_key,
                    )
                    LOGGER.debug(f"Cached response for query: {query[:100]}")
                except Exception as e:
                    LOGGER.warning(f"Cache write failed: {e}")

            return _response

        response = await _search_flight.do(flight_key, _execute_search)

        # Add final span attributes
        root_span.set_attribute("search.num_results_returned", len(response.get("results", [])))
        root_span.set_status(trace.StatusCode.OK)
        emit_observability_event(
            LOGGER,
            "tool.web_search.response",
            tool_name="web_search",
            cache_hit="miss",
            query=query,
            normalized_query=normalized_query,
            result_count=len(response.get("results", [])),
            providers_used=response.get("providers_used", []),
            results=response.get("results", []),
        )
        _record_tool_success(
            "web_search",
            input_query=query,
            output_result_count=len(response.get("results", [])),
        )

        # Report completion
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

    Returns:
    - `input_url`: exact URL provided by caller.
    - `normalized_url`: normalized URL used for cache lookup/storage and batch deduplication.
    - `fetched_url`: actual URL reached after redirects, if network fetch reached one.
    - `status`: success, partial, blocked, unsupported, or error.
    - `source_type`: detected source family such as html, pdf, github_issue, or wikipedia.
    - `fetch_backend`: backend strategy used, such as safe_http_extract, jina_reader, or browser_fallback.
    - `page_content`: bounded Markdown/text window.
    - `window`: pagination metadata with `has_more` and `next_offset`.
    """

    await ctx.info(f"Fetching: {url[:80]}...")
    emit_observability_event(
        LOGGER,
        "tool.get_content.request",
        tool_name="get_content",
        url=url,
        char_offset=char_offset,
        char_length=char_length,
        summary_mode=summary_mode,
    )

    max_length = _get_int_env("KINDLY_GET_CONTENT_MAX_CHARS", 50_000)
    safe_length = max(1, min(char_length, max_length))
    safe_offset = max(0, char_offset)
    safe_summary_mode = summary_mode if summary_mode in {"none", "brief", "detailed"} else "none"

    artifact = None
    normalized_url = canonicalize_url(url)
    try:
        cached = get_page_cache().lookup(normalized_url)
        if cached:
            artifact = {
                "input_url": url,
                "normalized_url": normalized_url,
                "fetched_url": None,
                "status": "success",
                "source_type": "cache",
                "fetch_backend": cached.get("extraction_method") or "cache",
                "content_type": "text/markdown",
                "markdown": cached["page_content"],
                "error": None,
            }
    except Exception as exc:
        LOGGER.warning(f"Page cache lookup failed: {exc}")

    if artifact is None:
        fetched = None
        try:
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
                )
            except Exception as exc:
                LOGGER.warning(f"Page cache store failed: {exc}")

    windowed = slice_content(
        artifact["markdown"],
        offset=safe_offset,
        length=safe_length,
    )
    summary = await create_summary(windowed.content, mode=safe_summary_mode, focus_query=focus_query)

    response = GetContentResponse(
        input_url=url,
        normalized_url=artifact["normalized_url"],
        fetched_url=artifact["fetched_url"],
        status=artifact["status"],
        source_type=artifact["source_type"],
        fetch_backend=artifact["fetch_backend"],
        page_content=windowed.content,
        window=windowed.window.__dict__,
        content_type=artifact["content_type"],
        error=artifact["error"],
        summary=summary,
    ).model_dump(exclude_none=True)
    response.setdefault("fetched_url", None)

    await ctx.info(
        f"Fetched status={response['status']} chars={len(response['page_content'])} has_more={response['window']['has_more']}"
    )
    emit_observability_event(
        LOGGER,
        "tool.get_content.response",
        tool_name="get_content",
        url=url,
        status=response["status"],
        content_length=len(response["page_content"]),
        content_preview=preview_text(response["page_content"]),
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

    Returns:
    - results: per-URL structured statuses with page_content and window metadata.
    - total_requested, total_returned, total_chars_returned.
    - has_more and cursor. If has_more is true, call again with cursor.

    This tool isolates failures per URL and keeps payloads bounded.
    """
    max_urls = _get_int_env("KINDLY_BATCH_GET_CONTENT_MAX_URLS", 30)
    bounded_urls = urls[: max(1, max_urls)]
    safe_concurrency = max(1, min(max_concurrency, 8))
    safe_item_length = max(500, min(per_item_char_length, _get_int_env("KINDLY_GET_CONTENT_MAX_CHARS", 50_000)))
    safe_total_budget = max(2_000, min(total_char_budget, _get_int_env("KINDLY_BATCH_TOTAL_CHAR_BUDGET_MAX", 300_000)))

    emit_observability_event(
        LOGGER,
        "tool.batch_get_content.request",
        tool_name="batch_get_content",
        urls=bounded_urls,
        url_count=len(bounded_urls),
        max_concurrency=safe_concurrency,
        per_item_char_length=safe_item_length,
        total_char_budget=safe_total_budget,
        has_cursor=bool(cursor),
    )

    await ctx.info(
        f"Batch fetching {len(bounded_urls)} URLs (concurrency={safe_concurrency}, budget={safe_total_budget})..."
    )
    output = await run_batch_fetch(
        urls=bounded_urls,
        params=BatchParams(
            max_concurrency=safe_concurrency,
            per_item_char_length=safe_item_length,
            total_char_budget=safe_total_budget,
            per_url_timeout_seconds=max(10.0, _resolve_tool_total_timeout_seconds() / max(len(bounded_urls), 1)),
        ),
        cursor=cursor,
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
    await ctx.info(
        f"Fetched {success_count}/{len(response['results'])} in this page; has_more={response['has_more']}"
    )
    emit_observability_event(
        LOGGER,
        "tool.batch_get_content.response",
        tool_name="batch_get_content",
        url_count=len(bounded_urls),
        success_count=success_count,
        error_count=len(response["results"]) - success_count,
        results=response["results"],
        has_more=response["has_more"],
    )
    _record_tool_success(
        "batch_get_content",
        input_url_count=len(bounded_urls),
        output_result_count=len(response["results"]),
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
    query: str, structured_output: bool = False, research_goal: str | None = None
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
    emit_observability_event(
        LOGGER,
        "tool.gemini_search.request",
        tool_name="gemini_search",
        query=query,
        structured_output=structured_output,
        research_goal=research_goal,
    )
    try:
        result = await gemini_search_with_grounding(
            query, structured_output=structured_output, research_goal=research_goal
        )
        response = result.model_dump(exclude_none=True)
        emit_observability_event(
            LOGGER,
            "tool.gemini_search.response",
            tool_name="gemini_search",
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
            output_content=response.get("answer") if isinstance(response.get("answer"), str) else None,
        )
        return response
    except Exception as exc:
        emit_observability_event(
            LOGGER,
            "tool.gemini_search.error",
            level=logging.WARNING,
            tool_name="gemini_search",
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
async def perplexity_search(query: str, depth: str = "normal", research_goal: str | None = None) -> dict:
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
    emit_observability_event(
        LOGGER,
        "tool.perplexity_search.request",
        tool_name="perplexity_search",
        query=query,
        depth=depth,
        research_goal=research_goal,
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
        emit_observability_event(
            LOGGER,
            "tool.perplexity_search.response",
            tool_name="perplexity_search",
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
        return response
    except ValueError as e:
        _record_tool_failure("perplexity_search")
        return format_tool_error(e, provider="perplexity")
    except httpx.HTTPError as e:
        LOGGER.warning(f"Perplexity search failed: {e}")
        emit_observability_event(
            LOGGER,
            "tool.perplexity_search.error",
            level=logging.WARNING,
            tool_name="perplexity_search",
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
        emit_observability_event(
            LOGGER,
            "tool.perplexity_search.error",
            level=logging.WARNING,
            tool_name="perplexity_search",
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
            LOGGER.info(f"Truncated transcript to {max_chars} chars for video {video_id}")

        duration_seconds = calculate_total_duration(segments)

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

    try:
        results = await search_youtube_videos(query, num_results=num_results)

        return YouTubeSearchResponse(
            query=query,
            results=results,
        ).model_dump(exclude_none=True)

    except YouTubeSearchError as e:
        return {
            "query": query,
            "results": [],
            "error": str(e),
            "isError": True,
            "error_type": "network",
            "action": "YouTube search via SearXNG failed. Check SEARXNG_BASE_URL configuration.",
        }

    except Exception as e:
        LOGGER.warning(f"YouTube search unexpected error: {e}")
        return format_tool_error(e, provider="youtube")


# ============ RESOURCES ============

@mcp.resource("status://providers")
def get_providers_status() -> str:
    """Which search providers are configured."""
    lines = [
        "# Search Provider Status",
        "",
        f"**SearXNG** (Primary): {'✓ Configured' if os.environ.get('SEARXNG_BASE_URL') else '✗ Not configured'}",
        f"**Tavily**: {'✓ Configured' if os.environ.get('TAVILY_API_KEY') else '✗ Not configured'}",
        f"**Brave**: {'✓ Configured' if os.environ.get('BRAVE_API_KEY') else '✗ Not configured'}",
        f"**Jina**: {'✓ Configured' if os.environ.get('JINA_API_KEY') else '✗ Not configured'}",
        f"**Composio LLM Search**: {'✓ Configured' if os.environ.get('COMPOSIO_API_KEY') and os.environ.get('KINDLY_COMPOSIO_USER_ID') else '✗ Not configured'}",
        "",
        "## AI Search",
        f"**Gemini**: {'✓ Configured' if settings.gemini_api_key else '✗ Not configured'}",
        f"**Perplexity (Pollinations)**: {'✓ Configured' if os.environ.get('POLLINATIONS_API_KEY') else '✗ Not configured'}",
        "",
        "## Other",
        f"**GitHub Token**: {'✓ Configured' if os.environ.get('GITHUB_TOKEN') else '✗ Not configured'}",
    ]
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
5. Use gemini_search for quick grounded synthesis.
6. Use perplexity_search only after the query is narrowed to one topic.
7. Use youtube_search before youtube_transcript.
8. Use composio_similarlinks to expand from a known good URL.

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
| gemini_search | Quick grounded answers |
| perplexity_search | Deep reasoning synthesis |
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

@mcp.prompt
def debug_error_prompt(error_message: str) -> str:
    """Prompt for debugging an error using web search."""
    return f"""Debug this error: {error_message}

Approach:
1. Search the exact error message in quotes with rewrite=False
2. Check GitHub issues for similar reports
3. Verify library versions match solution
4. Use batch_get_content for 3+ candidate issues/docs
5. Apply fix and test

Start: web_search(query="{error_message}", research_goal="Debug exact error and find reproducible fix", rewrite=False)"""


@mcp.prompt
def research_topic_prompt(topic: str, depth: str = "comprehensive") -> str:
    """Prompt for researching a topic."""
    return f"""Research: {topic} (depth: {depth})

Workflow:
1. web_search(query="{topic}", research_goal="Discover authoritative sources for research", num_results=5, rewrite=True)
2. If 3+ URLs look useful, batch_get_content(urls=[...]); otherwise get_content(url=...)
3. Check window.has_more or batch has_more/cursor before assuming a source is fully read
4. gemini_search(query="{topic} summary", research_goal="Synthesize findings with citations")
5. For deep synthesis, refine to one focused question before perplexity_search
6. If the topic is video/tutorial-heavy, youtube_search first, then youtube_transcript

Focus on: official docs, GitHub repos, recent updates"""


@mcp.prompt
def find_library_docs_prompt(library: str, feature: str) -> str:
    """Prompt for finding library documentation."""
    return f"""Find docs for: {library} - {feature}

1. web_search(query="{library} {feature} official docs", research_goal="Find official docs and current API examples", rewrite=True)
2. get_content on official docs URL
3. If multiple relevant docs pages are found, batch_get_content(urls=[...])
4. Use window.next_offset if the docs page is truncated
5. gemini_search for quick syntax reference only after source URLs are known
6. Use composio_similarlinks on a good docs URL to find adjacent docs pages

Prefer official docs over blog posts"""
