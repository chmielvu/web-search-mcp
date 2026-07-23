from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastmcp.server.context import Context

from ..utils.environment import get_float_env, get_int_env


from ..search.outcomes import drain_search_outcomes
from ..settings import settings
from ..telemetry import record_mcp_tool_call, record_tool_details
from ..telemetry.init import shutdown_telemetry
from ..utils.background_tasks import cancel_all_background_tasks, drain_background_tasks
from ..analytics.async_writes import shutdown_duckdb_write_executor
from ..content.firecrawl_stage import close_firecrawl_client
from ..content.remote_clients import close_crawl4ai_client, close_camoufox_client
from ..utils.http_client import close_http_client
from ..utils.public_output import serialize_public_web_search_response
from ..utils.singleflight import SingleFlight

LOGGER = logging.getLogger(__name__)

_academic_search_flight = SingleFlight()


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


def _resolve_session_id(ctx: Context | None) -> str | None:
    if ctx is None:
        return None
    fastmcp_context = getattr(ctx, "fastmcp_context", None)
    if fastmcp_context is not None:
        session_id = getattr(fastmcp_context, "session_id", None)
        if session_id:
            return str(session_id)
        client_id = getattr(fastmcp_context, "client_id", None)
        if client_id:
            return str(client_id)
    return None


def _public_settings_snapshot() -> dict[str, object]:
    """Return a safe subset of runtime settings for MCP clients."""
    return {
        "tool_surface": {
            "profile": settings.tool_profile,
            "tool_search_enabled": settings.tool_search_enabled,
        },
        "features": {
            "reranking_enabled": settings.rerank_entity_overlap_enabled,
            "judge_evaluation_enabled": settings.judge_evaluation_enabled,
            "entity_extraction_enabled": settings.entity_extraction_enabled,
            "analytics_enabled": settings.analytics_enabled,
            "query_decomposition_enabled": settings.query_decomposition_enabled,
        },
        "providers_configured": {
            "searxng": bool(os.environ.get("SEARXNG_BASE_URL")),
            "tavily": bool(os.environ.get("TAVILY_API_KEY")),
            "brave": bool(os.environ.get("BRAVE_API_KEY")),
            "jina": bool(os.environ.get("JINA_API_KEY")),
            "langsearch": bool(os.environ.get("LANGSEARCH_API_KEY")),
            "cohere": bool(settings.cohere_api_key),
            "openrouter": bool(settings.openrouter_api_key),
            "gemini": bool(settings.gemini_api_key),
            "voyage": bool(settings.voyage_api_key),
            "composio": bool(
                os.environ.get("COMPOSIO_API_KEY") and os.environ.get("COMPOSIO_USER_ID")
            ),
            "search_router": bool(os.environ.get("SEARCH_ROUTER_API_KEY")),
            "github_token": bool(os.environ.get("GITHUB_TOKEN")),
        },
        "models": {
            "cohere_rerank_model": settings.cohere_rerank_model,
            "openrouter_rerank_model": settings.openrouter_rerank_model,
            "voyage_rerank_model": settings.voyage_rerank_model,
            "jina_rerank_model": settings.jina_rerank_model,
            "judge_model": settings.judge_model,
            "rankllm_timeout_seconds": settings.rankllm_timeout_seconds,
            "grok_model": settings.grok_model,
            "gliner_model": settings.gliner_model,
        },
        "timeouts_seconds": {
            "tool_total": _resolve_tool_total_timeout_seconds(),
            "query_understanding": settings.query_classifier_timeout_seconds,
            "query_decomposition": settings.query_decomposition_timeout_seconds,
            "youtube_transcript": settings.youtube_transcript_timeout_seconds,
            "grok": settings.grok_timeout_seconds,
            "cohere_rerank": settings.cohere_rerank_timeout,
            "openrouter_rerank": settings.openrouter_rerank_timeout,
            "judge": settings.judge_timeout_seconds,
        },
    }


def _analytics_schema_snapshot() -> dict[str, object]:
    from ..analytics.app import _OBJECT_DESCRIPTIONS

    return {
        "analytics_db_path": settings.analytics_duckdb_path,
        "object_count": len(_OBJECT_DESCRIPTIONS),
        "objects": _OBJECT_DESCRIPTIONS,
    }


def _analytics_report_snapshot(
    report_name: str,
    *,
    days: int = 7,
) -> dict[str, object]:
    from ..analytics.formatting import json_safe_rows
    from ..analytics.reports import available_reports, run_report

    table = run_report(report_name, days=days)
    return {
        "report": report_name,
        "days": days,
        "row_count": table.num_rows,
        "rows": json_safe_rows(table.to_pylist()),
        "available_reports": available_reports(),
    }


@asynccontextmanager
async def _app_lifespan(app: object) -> AsyncIterator[dict]:
    """Drain outcomes before persistence, transport, and telemetry shutdown."""
    del app
    yield {}
    try:
        await drain_search_outcomes(settings.analytics_shutdown_drain_timeout_seconds)
        await drain_background_tasks(
            name_prefixes=("analytics.",),
            timeout_seconds=settings.analytics_shutdown_drain_timeout_seconds,
        )
        shutdown_duckdb_write_executor(wait=True)
    except Exception as exc:
        LOGGER.warning("Error draining persistence during shutdown: %s", type(exc).__name__)
    try:
        await close_http_client()
    except Exception as exc:
        LOGGER.warning("Error closing shared HTTP client during shutdown: %s", type(exc).__name__)
    try:
        await close_crawl4ai_client()
        await close_camoufox_client()
        await close_firecrawl_client()
    except Exception as exc:
        LOGGER.warning(
            "Error closing remote content clients during shutdown: %s", type(exc).__name__
        )
    try:
        await cancel_all_background_tasks()
    except Exception as exc:
        LOGGER.warning("Error cancelling background tasks during shutdown: %s", type(exc).__name__)
    shutdown_telemetry()


# ---------------------------------------------------------------------------
# Backward-compat re-export: tools/content.py and transitive server-touching
# tests still import get_int_env from this module. The canonical helper lives
# in utils.environment; aliasing keeps the public name stable without
# churning call sites.
# ---------------------------------------------------------------------------
_get_int_env = get_int_env
_get_float_env = get_float_env


def _resolve_tool_total_timeout_seconds() -> float:
    """
    Resolve the total per-tool time budget (seconds).

    Historically this was clamped to <=55s to stay below common 60s tool-call limits.
    In practice, Windows headless-browser cold starts can exceed that, so we allow a
    higher cap that can be tuned via environment variables.
    """
    value = get_float_env("TOOL_TOTAL_TIMEOUT_SECONDS", 120.0)
    max_value = get_float_env("TOOL_TOTAL_TIMEOUT_MAX_SECONDS", 600.0)
    safe_max = max(1.0, max_value)
    return max(1.0, min(value, safe_max))


def _timeout_markdown_note(url: str, *, scope: str | None = None) -> str:
    detail = f": {scope}" if scope else ""
    return f"_Failed to retrieve page content: TimeoutError{detail}_\n\nSource: {url}\n"


def _resolve_web_search_max_concurrency(num_results: int) -> int:
    raw_env = (os.environ.get("WEB_SEARCH_MAX_CONCURRENCY") or "").strip()
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
