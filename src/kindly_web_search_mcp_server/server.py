# ruff: noqa: E402
from __future__ import annotations

# Load .env file before any other imports that read environment variables
from pathlib import Path
import os
import threading

from dotenv import load_dotenv

# Look for .env in the package directory and parent directories
_package_dir = Path(__file__).parent
_project_root = _package_dir.parent.parent  # web-search-mcp root
load_dotenv(_project_root / ".env")
load_dotenv()  # Also try cwd as fallback
os.environ.setdefault("FASTMCP_BANNER", "false")
os.environ.setdefault("FASTMCP_SHOW_SERVER_BANNER", "false")
os.environ.setdefault("FASTMCP_LOG_LEVEL", "WARNING")

# Initialize OpenTelemetry in a background daemon thread. Importing
# Phoenix (which transitively imports sklearn/scipy's C extensions) costs
# ~25-30s even with zero contention (measured in isolation) -- far too
# slow to run synchronously on the stdio handshake path, and no tool call
# may ever await it (see _ensure_telemetry's docstring below).
import logging

from .composio_tools import register_composio_tools
from .quick_web_search import register_quick_web_search
from .settings import settings
from .tools._helpers import (
    _app_lifespan,
    _resolve_tool_total_timeout_seconds,  # noqa: F401  re-exported for tests
    _resolve_web_search_max_concurrency,  # noqa: F401  re-exported for tests
)
from .tools.academic import academic_search
from .tools.ai_search import gemini_search, grok_search
from .tools.content import batch_get_content, discover_links, get_content
from .tools.profiles import apply_tool_profile
from .tools.catalog import tool_kwargs
from .tools.prompts import (
    query_refinement_prompt,
    research_methodology_prompt,
    web_search_workflow_prompt,
)
from .tools.resources import (
    get_analytics_report_resource,
    get_analytics_schema_resource,
    get_candidate_survival_resource,
    get_features_status_resource,
    get_providers_status_resource,
    get_public_settings_resource,
    get_workflow_doc_resource,
)
from .tools.search import web_search
from .tools.sitemap import generate_sitemap
from .tools.youtube import youtube_search, youtube_transcript
from .utils.logging import configure_logging
from .utils.observability import emit_observability_event

from .analytics.app import analytics_app

configure_logging()
LOGGER = logging.getLogger(__name__)

# tqdm spawns a monitor thread (Thread.start()) the first time any tqdm
# instance is created in this process, regardless of disable=True /
# show_progress=False (tqdm.std.tqdm.__new__ checks monitor_interval, not
# the disable kwarg). Under heavy GIL contention -- e.g. a concurrent
# import of a large C extension -- that Thread.start() call can stall for
# a very long time. bm25s and rank_llm both instantiate tqdm during the
# web_search rerank stage, so disable the monitor thread globally; it only
# exists to force-refresh stalled progress bars, which are irrelevant here.
import tqdm as _tqdm_pkg

_tqdm_pkg.tqdm.monitor_interval = 0

_telemetry_init_lock = threading.Lock()
_telemetry_init_started = False


def _ensure_telemetry() -> None:
    """Start telemetry initialization in a background daemon thread (idempotent).

    Fire-and-forget by design. Telemetry is best-effort observability, so no
    tool call may ever await this -- an earlier revision made
    ``on_call_tool`` block on it, which just moved the ~25-30s Phoenix
    import cost onto the first tool call's latency budget instead of
    removing it. This is called as early as possible in ``main()`` purely
    to give that import maximum head start before real traffic arrives.
    """
    global _telemetry_init_started
    with _telemetry_init_lock:
        if _telemetry_init_started:
            return
        _telemetry_init_started = True

    def _run() -> None:
        try:
            from .telemetry import init_telemetry_background

            init_telemetry_background(service_name="web-search-mcp")
        except Exception:
            LOGGER.warning("Telemetry background init failed", exc_info=True)

    threading.Thread(target=_run, name="telemetry-init", daemon=True).start()


import argparse
import sys
from typing import Literal

from fastmcp import FastMCP

mcp = FastMCP(
    "web-search",
    version="0.1.8",
    lifespan=_app_lifespan,
    providers=[analytics_app],
    instructions=(
        "WEB SEARCH METHODOLOGY\n"
        "\n"
        "Decompose. Do not search the user's question verbatim. Break it into\n"
        "2-4 sub-queries exploring different angles (definitions, comparisons,\n"
        "current state, opposing views). Each sub-query should target a different\n"
        "information gap.\n"
        "\n"
        "Reconnaissance first. Start every research task with quick_web_search\n"
        "to map the topic landscape and discover terminology you didn't know.\n"
        "Follow with gemini_search for a quick grounded synthesis. Only then\n"
        "move to web_search for deep discovery.\n"
        "\n"
        "Iterate — never stop at round one. Evaluate round-1 results for gaps:\n"
        "missing perspectives, single-source claims, outdated dates, domain\n"
        "concentration. Formulate better queries from what you learned and\n"
        "search again. At least two rounds before concluding.\n"
        "\n"
        "Deep-read the best sources. After discovery, use get_content (single)\n"
        "or batch_get_content (3+ URLs) on the most promising results. Judge\n"
        "by provider consensus (provider_count >= 2), domain authority, and\n"
        "snippet specificity. Do not trust snippets alone — read the page.\n"
        "\n"
        "Know when enough is enough. Terminate when 3 independent sources\n"
        "agree on key claims, or when 2 consecutive search rounds add nothing\n"
        "new. Announce your verdict: what's well-supported, what's contested,\n"
        "what's unknown.\n"
        "\n"
        "Tool routing: quick_web_search/gemini_search -> web_search ->\n"
        "composio_similarlinks -> get_content/batch_get_content -> iterate.\n"
        "Use discover_links to explore link graphs. Use academic_search for\n"
        "scholarly questions. Use youtube_search + youtube_transcript for\n"
        "video content.\n"
        "\n"
        "For deeper guidance, request the research_methodology prompt.\n"
        "For the tool routing reference card, read docs://workflow."
    ),
)
# Add stdout guard middleware FIRST so it wraps every other middleware and
# the tool call itself. MCP's stdio transport captures sys.stdout.buffer
# once at startup and treats it as a newline-delimited JSON-RPC channel;
# any stray print() anywhere in the tool-call path (rank_llm's reranker
# prints "Template validated successfully!", "Loading {model} ...", etc.
# directly to stdout) corrupts that stream and the client hangs waiting
# for a response line that will never parse -- explaining "Transport
# closed"/timeouts on web_search specifically (the only tool that reaches
# rank_llm) while fast tools on the same transport are unaffected.
from .middleware import create_stdout_guard_middleware

mcp.add_middleware(create_stdout_guard_middleware())

# Add argument aliasing middleware (rewrites hallucinated parameters before validation)
from .middleware import create_argument_aliasing_middleware

mcp.add_middleware(create_argument_aliasing_middleware())

# Add expensive tool protection middleware
# Implements "think first, then call expensive tool" pattern
from .middleware import create_expensive_tool_middleware

mcp.add_middleware(create_expensive_tool_middleware())

# Add differentiated rate limiting:
# - Higher throughput for lightweight tools (web_search/get_content/gemini_search)
# - Stricter quota for expensive tools (grok_search)
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
register_quick_web_search(mcp)
register_composio_tools(mcp)

# Expose prompts and resources as tools for clients that only support the tools protocol.
from fastmcp.server.transforms import PromptsAsTools, ResourcesAsTools

mcp.add_transform(PromptsAsTools(mcp))
mcp.add_transform(ResourcesAsTools(mcp))


# Register tools
mcp.tool(**tool_kwargs("web_search"))(web_search)
mcp.tool(**tool_kwargs("get_content"))(get_content)
mcp.tool(**tool_kwargs("batch_get_content"))(batch_get_content)
mcp.tool(**tool_kwargs("discover_links"))(discover_links)
mcp.tool(**tool_kwargs("gemini_search"))(gemini_search)
mcp.tool(**tool_kwargs("grok_search"))(grok_search)
mcp.tool(**tool_kwargs("youtube_transcript"))(youtube_transcript)
mcp.tool(**tool_kwargs("youtube_search"))(youtube_search)
mcp.tool(**tool_kwargs("generate_sitemap"))(generate_sitemap)
mcp.tool(**tool_kwargs("academic_search"))(academic_search)


# Register resources
mcp.resource(
    "status://providers", tags={"status", "diagnostic"}, annotations={"readOnlyHint": True}
)(get_providers_status_resource)
mcp.resource(
    "status://features", tags={"status", "diagnostic"}, annotations={"readOnlyHint": True}
)(get_features_status_resource)
mcp.resource("docs://workflow", tags={"docs", "help"}, annotations={"readOnlyHint": True})(
    get_workflow_doc_resource
)
mcp.resource(
    "settings://public", tags={"config", "diagnostic"}, annotations={"readOnlyHint": True}
)(get_public_settings_resource)
mcp.resource(
    "analytics://schema", tags={"analytics", "diagnostic"}, annotations={"readOnlyHint": True}
)(get_analytics_schema_resource)
mcp.resource(
    "analytics://candidate-survival",
    tags={"analytics", "diagnostic"},
    annotations={"readOnlyHint": True},
)(get_candidate_survival_resource)
mcp.resource(
    "analytics://reports/{report_name}{?days}",
    tags={"analytics", "diagnostic"},
    annotations={"readOnlyHint": True},
)(get_analytics_report_resource)


# Register prompts
mcp.prompt(
    name="web_search_workflow",
    description="Guided research workflow with depth/focus routing.",
    tags={"research", "workflow"},
    version="1.0",
)(web_search_workflow_prompt)
mcp.prompt(
    name="query_refinement",
    description="Plan query variants and rewrites after a failed or sparse search.",
    tags={"research", "workflow"},
    version="1.0",
)(query_refinement_prompt)
mcp.prompt(
    name="research_methodology",
    description="Complete web research methodology: decomposition, iteration, evaluation, termination criteria, and anti-patterns. Request when starting complex multi-round investigations.",
    tags={"research", "workflow"},
    version="1.0",
)(research_methodology_prompt)


Transport = Literal["stdio", "sse", "streamable-http"]


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-web-search",
        description="MCP server: Multi-provider web search (SearXNG/Tavily/Brave/Jina) with RRF merge.",
    )

    # Accept `start-mcp-server` as a no-op positional arg for compatibility
    # with the `web-search` entry point when launched by MCP clients
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
    resolved_port_raw = str(port) if port is not None else os.environ.get("FASTMCP_PORT", "8000")
    try:
        resolved_port = int(resolved_port_raw)
    except ValueError:
        resolved_port = 8000
    return resolved_host, resolved_port


def _warm_heavy_imports() -> None:
    """Pre-import the inference router module before server startup.

    ``inference.router`` imports ``openai.resources.chat``, which contends with
    other lazy imports for the global import lock under stdio transport.
    Calling this from ``main()`` before ``mcp.run()`` ensures the work
    happens during server startup, not during the first tool call.
    """
    import importlib

    importlib.import_module(".inference.router", package=__package__)


def main(argv: list[str] | None = None) -> None:
    # Prevent native BLAS libraries (OpenBLAS, MKL, Accelerate) from spawning
    # their own thread pools.  Without this, simultaneous calls into numpy/scipy
    # from asyncio tasks and thread-pool executors can corrupt internal BLAS
    # state on Windows, causing STATUS_ACCESS_VIOLATION crashes.
    for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(_key, "1")

    # Set high recursion limit for deep query trees if needed
    sys.setrecursionlimit(2000)

    # Force any late-installed default logs to stderr without lowering LOG_LEVEL.
    import logging

    level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    logging.basicConfig(stream=sys.stderr, level=level)

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
        raise SystemExit(2)

    if transport in ("sse", "streamable-http"):
        host, port = _resolve_host_port(args.host, args.port)
        for key, value in (("host", host), ("port", port)):
            if hasattr(mcp, "settings") and hasattr(mcp.settings, key):  # type: ignore[attr-defined]
                setattr(mcp.settings, key, value)  # type: ignore[attr-defined]

    # Start telemetry first (background thread, non-blocking -- see
    # _ensure_telemetry) so its ~25-30s Phoenix import gets the maximum
    # possible head start before any tool call can arrive.
    _ensure_telemetry()
    _warm_heavy_imports()
    try:
        mcp.run(transport=transport, mount_path=args.mount_path, show_banner=False)
    except TypeError:
        try:
            mcp.run(transport=transport, show_banner=False)
        except TypeError:
            mcp.run(transport=transport)


apply_tool_profile(mcp, settings.tool_profile)

# Emit profile applied event (always, after visibility is set)
emit_observability_event(
    LOGGER,
    "tool_surface.profile_applied",
    profile=settings.tool_profile,
)

# Opt-in FastMCP tool search transform (per joint plan Task 2.2, no backward compat).
# When enabled, clients see only pinned + search_tools + call_tool meta-tools;
# underlying tools (respecting current profile) are discoverable via search.
if settings.tool_search_enabled:
    from fastmcp.server.transforms.search import RegexSearchTransform

    # Pin the core safe discovery tools so basic flows don't require a search roundtrip.
    # Search will still surface profile-specific tools (e.g. youtube_*, gemini_search)
    # because transform respects prior visibility gates.
    mcp.add_transform(RegexSearchTransform(always_visible=["web_search", "get_content"]))
    emit_observability_event(
        LOGGER,
        "tool_surface.search_enabled",
        enabled=True,
        profile=settings.tool_profile,
    )


if __name__ == "__main__":
    main()
