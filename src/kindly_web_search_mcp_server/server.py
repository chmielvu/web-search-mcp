# ruff: noqa: E402
from __future__ import annotations

# Load .env file before any other imports that read environment variables
from pathlib import Path
import os

from dotenv import load_dotenv

# Look for .env in the package directory and parent directories
_package_dir = Path(__file__).parent
_project_root = _package_dir.parent.parent  # web-search-mcp root
load_dotenv(_project_root / ".env")
load_dotenv()  # Also try cwd as fallback
os.environ.setdefault("FASTMCP_BANNER", "false")
os.environ.setdefault("FASTMCP_SHOW_SERVER_BANNER", "false")
os.environ.setdefault("FASTMCP_LOG_LEVEL", "WARNING")

# Initialize OpenTelemetry BEFORE any other imports
# This ensures all HTTP calls (httpx, etc.) are auto-instrumented.
# init_telemetry_background runs in a daemon thread so that an unreachable
# OTLP endpoint (Grafana Cloud) never blocks MCP startup (~70s hang).
import logging

from .agent.mcp import register_agentic_web_research_tools
from .composio_tools import register_composio_tools
from .settings import settings
from .telemetry import init_telemetry_background
from .tools._helpers import (
    _app_lifespan,
    _resolve_tool_total_timeout_seconds,  # noqa: F401  re-exported for tests
    _resolve_web_search_max_concurrency,  # noqa: F401  re-exported for tests
)
from .tools.academic import academic_search
from .tools.ai_search import gemini_search, grok_search
from .tools.catalog import tool_kwargs
from .tools.content import batch_get_content, discover_links, get_content
from .tools.profiles import apply_tool_profile
from .tools.prompts import web_search_workflow_prompt
from .tools.resources import (
    get_analytics_report_resource,
    get_analytics_schema_resource,
    get_cache_hit_rates_resource,
    get_cache_stats_resource,
    get_candidate_survival_resource,
    get_public_settings_resource,
)
from .tools.search import web_search
from .tools.sitemap import generate_sitemap
from .tools.status import get_features_status, get_providers_status
from .tools.workflow import get_workflow_doc
from .tools.youtube import youtube_search, youtube_transcript
from .utils.logging import configure_logging
from .utils.observability import emit_observability_event

configure_logging()
init_telemetry_background(service_name="web-search-mcp")
LOGGER = logging.getLogger(__name__)

import argparse
import sys
from types import MethodType
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError

mcp = FastMCP(
    "web-search",
    lifespan=_app_lifespan,
    instructions=(
        "Use quick_web_search for initial reconnaissance. Use web_search for discovery "
        "with rewrite=true by default. Use get_content for one known URL; "
        "use batch_get_content for 3+ URLs. When you need a summary, set "
        "summary_mode=brief or summary_mode=detailed and add focus_query if helpful."
    ),
)

# Add expensive tool protection middleware
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
register_agentic_web_research_tools(mcp)


_base_list_resources = mcp.list_resources
_base_list_resource_templates = mcp.list_resource_templates
_base_read_resource = mcp.read_resource
_base_list_prompts = mcp.list_prompts
_base_render_prompt = mcp.render_prompt


async def _compat_list_resources(self: FastMCP, *, run_middleware: bool = True) -> list[Any]:  # type: ignore[override]
    """Merge local function resources with the public FastMCP resource list.

    FastMCP 3.4.0 exposes local `@mcp.resource` entries via `_list_resources()`
    in this server, but the public `list_resources()` path currently returns only
    mounted app prefab resources. Keep the stock behavior, then append any missing
    no-auth local resources by URI.
    """
    listed = list(await _base_list_resources(run_middleware=run_middleware))
    existing_uris = {str(getattr(item, "uri", "")) for item in listed}
    for resource in await self._list_resources():
        uri = str(getattr(resource, "uri", ""))
        if not uri or uri in existing_uris:
            continue
        if getattr(resource, "auth", None) is not None:
            continue
        listed.append(resource)
        existing_uris.add(uri)
    return listed


async def _compat_read_resource(
    self: FastMCP,
    uri: str,
    *,
    version: object = None,
    run_middleware: bool = True,
    task_meta: object = None,
):
    """Fallback to local function-resource resolution when public lookup misses."""
    try:
        return await _base_read_resource(  # type: ignore[call-overload]
            uri,
            version=version,  # type: ignore[arg-type]
            run_middleware=run_middleware,
            task_meta=task_meta,  # type: ignore[arg-type]
        )
    except NotFoundError:
        resource = await self._get_resource(uri, version=version)  # type: ignore[arg-type]
        if resource is not None and getattr(resource, "auth", None) is None:
            return await resource._read(task_meta=task_meta)  # type: ignore[arg-type]

        template = await self._get_resource_template(uri, version=version)  # type: ignore[arg-type]
        if template is None or getattr(template, "auth", None) is not None:
            raise
        params = template.matches(uri)
        if params is None:
            raise
        return await template._read(uri, params, task_meta=task_meta)  # type: ignore[arg-type]


mcp.list_resources = MethodType(_compat_list_resources, mcp)
mcp.read_resource = MethodType(_compat_read_resource, mcp)


async def _compat_list_resource_templates(
    self: FastMCP, *, run_middleware: bool = True
) -> list[Any]:  # type: ignore[override]
    """Merge local function resource templates with the public template list."""
    listed = list(await _base_list_resource_templates(run_middleware=run_middleware))
    existing_uris = {str(getattr(item, "uri_template", "")) for item in listed}
    for template in await self._list_resource_templates():
        uri_template = str(getattr(template, "uri_template", ""))
        if not uri_template or uri_template in existing_uris:
            continue
        if getattr(template, "auth", None) is not None:
            continue
        listed.append(template)
        existing_uris.add(uri_template)
    return listed


mcp.list_resource_templates = MethodType(_compat_list_resource_templates, mcp)


async def _compat_list_prompts(self: FastMCP, *, run_middleware: bool = True) -> list[Any]:  # type: ignore[override]
    """Merge local function prompts with the public FastMCP prompt list."""
    listed = list(await _base_list_prompts(run_middleware=run_middleware))
    existing_names = {str(getattr(item, "name", "")) for item in listed}
    for prompt in await self._list_prompts():
        name = str(getattr(prompt, "name", ""))
        if not name or name in existing_names:
            continue
        if getattr(prompt, "auth", None) is not None:
            continue
        listed.append(prompt)
        existing_names.add(name)
    return listed


async def _compat_render_prompt(
    self: FastMCP,
    name: str,
    arguments: dict[str, object] | None = None,
    *,
    version: object = None,
    run_middleware: bool = True,
    task_meta: object = None,
):
    """Fallback to local function-prompt resolution when public lookup misses."""
    try:
        return await _base_render_prompt(  # type: ignore[call-overload]
            name,
            arguments,
            version=version,  # type: ignore[arg-type]
            run_middleware=run_middleware,
            task_meta=task_meta,  # type: ignore[arg-type]
        )
    except NotFoundError:
        prompt = await self._get_prompt(name, version=version)  # type: ignore[arg-type]
        if prompt is None or getattr(prompt, "auth", None) is not None:
            raise
        return await prompt._render(arguments, task_meta=task_meta)  # type: ignore[arg-type]


mcp.list_prompts = MethodType(_compat_list_prompts, mcp)
mcp.render_prompt = MethodType(_compat_render_prompt, mcp)


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
mcp.resource("status://providers")(get_providers_status)
mcp.resource("status://features")(get_features_status)
mcp.resource("docs://workflow")(get_workflow_doc)
mcp.resource("settings://public")(get_public_settings_resource)
mcp.resource("cache://stats")(get_cache_stats_resource)
mcp.resource("analytics://schema")(get_analytics_schema_resource)
mcp.resource("analytics://candidate-survival")(get_candidate_survival_resource)
mcp.resource("analytics://cache-hit-rates")(get_cache_hit_rates_resource)
mcp.resource("analytics://reports/{report_name}{?days}")(get_analytics_report_resource)


# Register prompts
mcp.prompt(
    name="web_search_workflow",
    description="Placeholder prompt — content to be written.",
    tags={"research", "workflow"},
)(web_search_workflow_prompt)


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


def main(argv: list[str] | None = None) -> None:
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
