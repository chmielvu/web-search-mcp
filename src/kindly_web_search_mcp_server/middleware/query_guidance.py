"""Per-tool result-aware guidance middleware.

Appends dynamic, context-specific guidance to tool results based on actual
return data — bridging server-internal knowledge the agent doesn't have
(specialized resolvers, available prompts, pagination mechanics).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from ..errors import classify_error
from .session_tracking import SessionTracker, get_session_id

from ..heuristics.guidance_messages import (
    format_shaping_guidance,
    web_search_empty_guidance,
    web_search_specialized_gap_guidance,
)

logger = logging.getLogger(__name__)
GEMINI_TOOLS = frozenset({"gemini_search"})
GEMINI_QUERY_ADVISORY = """
GEMINI SEARCH: Best for quick grounded synthesis. Use a single focused question, include exact API/error/version terms, and add recency hints when freshness matters. Use web_search plus fetch when you need to compare source pages yourself.
"""
_GEMINI_GUIDANCE_SESSION_TIMEOUT_SECONDS = 300


# ── Helpers ────────────────────────────────────────────────────────────

# Keys that indicate the dict is an actual tool response (not an envelope)
_RESPONSE_KEYS = frozenset({"results", "query", "page_content", "error", "answer"})


def _unwrap_fastmcp_result(data: dict) -> dict:
    """Unwrap FastMCP's ``{"result": ...}`` envelope for union-typed tool returns.

    FastMCP wraps non-object JSON schemas (e.g. ``WebSearchResponse | ToolErrorResponse``)
    in ``{"result": <actual_response>}``.  The guidance generators need the inner dict.
    """
    if not isinstance(data, dict):
        return data
    inner = data.get("result")
    if isinstance(inner, dict) and any(k in inner for k in _RESPONSE_KEYS):
        return inner
    return data


def _append_enrichment(
    result: Any,
    source: str,
    message: str,
    next_tools: list[str] | None = None,
    next_prompts: list[str] | None = None,
) -> Any:
    """Attach guidance + suggested_next_tools + suggested_prompts to ToolResult."""
    if not isinstance(result, ToolResult) or not isinstance(result.structured_content, dict):
        return result

    structured = dict(result.structured_content)
    ag = list(structured.get("agent_guidance") or [])
    ag.append({"source": source, "message": message.strip()})
    structured["agent_guidance"] = ag
    if next_tools:
        existing_tools = list(structured.get("suggested_next_tools") or [])
        existing_tools.extend(next_tools)
        structured["suggested_next_tools"] = existing_tools
    if next_prompts:
        existing_prompts = list(structured.get("suggested_prompts") or [])
        existing_prompts.extend(next_prompts)
        structured["suggested_prompts"] = existing_prompts
    return ToolResult(structured_content=structured, meta=result.meta, is_error=result.is_error)


def _extract_domain(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else ""


def _has_domain(urls: list[str], pattern: str) -> bool:
    return any(re.search(pattern, u) for u in urls)


# ── Per-tool guidance generators ──────────────────────────────────────


def _gemini_is_available() -> bool:
    """Check whether the gemini provider is configured and reachable."""
    try:
        from ..search.provider_registry import PROVIDER_DEFINITIONS, provider_is_reachable  # noqa: PLC0415

        definition = PROVIDER_DEFINITIONS.get("gemma")
        return definition is not None and provider_is_reachable(definition)
    except Exception:
        return False


def _guide_web_search(data: dict) -> tuple[str, list[str], list[str]]:
    data = _unwrap_fastmcp_result(data)
    results = data.get("results", [])
    providers = data.get("providers_used", [])
    urls = [r.get("link", "") for r in results]
    next_tools: list[str] = []
    next_prompts: list[str] = ["research_methodology"]
    parts: list[str] = []
    gemini_ok = _gemini_is_available()
    intent = data.get("intent")
    shaping = data.get("query_shaping") or []

    if not results:
        guidance, tools = web_search_empty_guidance(
            intent=intent if isinstance(intent, str) else None,
            providers_used=providers if isinstance(providers, list) else [],
            query=str(data.get("query") or ""),
            shaping=shaping if isinstance(shaping, list) else [],
        )
        if gemini_ok:
            if "gemini_search" not in tools:
                guidance += " Or try gemini_search for a grounded answer without URLs."
                tools.append("gemini_search")
        elif "gemini_search" in tools:
            # empty_guidance may suggest gemini; drop if unavailable
            tools = [t for t in tools if t != "gemini_search"]
        return (guidance, tools, next_prompts)

    parts.append(f"{len(results)} results from {len(providers)} providers.")

    shape_msg = format_shaping_guidance(shaping if isinstance(shaping, list) else [])
    if shape_msg:
        parts.append(shape_msg)

    gap_msg, gap_tools = web_search_specialized_gap_guidance(
        intent=intent if isinstance(intent, str) else None,
        providers_used=providers if isinstance(providers, list) else [],
        results=results if isinstance(results, list) else [],
    )
    if gap_msg:
        parts.append(gap_msg)
    for t in gap_tools:
        if t not in next_tools:
            next_tools.append(t)

    # Feature surfacing: point out domains that get specialized treatment
    special = []
    if _has_domain(urls, r"github\.com"):
        special.append("github.com (specialized issue/PR resolver)")
    if _has_domain(urls, r"stackoverflow\.com|stackexchange\.com"):
        special.append("StackExchange (specialized Q&A resolver)")
    if _has_domain(urls, r"wikipedia\.org"):
        special.append("Wikipedia (specialized resolver)")
    if special:
        parts.append("URLs from: " + ", ".join(special) + ".")
        next_tools.append("composio_similarlinks")

    # Domain concentration
    domains = {_extract_domain(u) for u in urls if u}
    if len(domains) == 1 and len(results) >= 3:
        parts.append(f"All results from {list(domains)[0]}.")
        if gemini_ok:
            parts.append("Try gemini_search for broader coverage.")
            next_tools.append("gemini_search")

    # Provider agreement
    top_pc = max((r.get("provider_count", 0) for r in results[:3]), default=0)
    if top_pc <= 1 and len(providers) > 1:
        parts.append("Top results from single provider — cross-check.")
        if gemini_ok:
            next_tools.append("gemini_search")

    return (" ".join(parts), next_tools, next_prompts)


def _guide_fetch(data: dict) -> tuple[str, list[str], list[str]]:
    data = _unwrap_fastmcp_result(data)
    parts: list[str] = []
    next_tools: list[str] = []
    next_prompts: list[str] = ["research_methodology"]
    results = data.get("results", [])
    if not isinstance(results, list):
        results = []

    if data.get("mode") == "single":
        item = results[0] if results else data
        source_type = item.get("source_type", "")
        fetch_backend = item.get("fetch_backend", "")
        window = item.get("window", {})
        status = item.get("status", "")
        content_len = len(item.get("page_content", ""))
        if window.get("has_more"):
            nxt = window.get("next_offset", 0)
            parts.append(f"Truncated at {nxt} chars. Continue: fetch(offset={nxt}).")
            next_tools.append("fetch")
        if source_type == "github_issue":
            parts.append("GitHub issue detected. Use composio_similarlinks to find related issues/PRs.")
            next_tools.append("composio_similarlinks")
        elif source_type == "wikipedia":
            parts.append("Wikipedia source. Cross-reference with academic_search or official docs.")
            next_tools.append("academic_search")
        if fetch_backend == "browser_fallback":
            parts.append("Used browser fallback (JS-heavy page). Content may be less complete.")
        if content_len < 300 and not window.get("has_more") and status != "error":
            parts.append("Very short content (possibly behind login/paywall). Try an alternative source.")
        wall = item.get("wall")
        if isinstance(wall, dict) and wall.get("kind") in {"login", "paywall", "bot"}:
            parts.append(f"Access signal detected: {wall['kind']}. Do not trust the returned wall content.")
        return (" ".join(parts) if parts else "", next_tools, next_prompts)

    has_more = data.get("has_more", False)
    cursor = data.get("cursor")
    total_req = data.get("total_requested", 0)
    if has_more and cursor:
        remaining = max(0, int(total_req or 0) - len(results))
        parts.append(
            f"has_more=true ({remaining} URLs pending). Continue: fetch(cursor={cursor})."
        )
        next_tools.append("fetch")

    success_count = sum(1 for item in results if item.get("status") == "success")
    if total_req > 0 and success_count < total_req:
        parts.append(f"{success_count}/{total_req} URLs succeeded in this page.")

    source_types = {item.get("source_type", "") for item in results if item.get("status") == "success"}
    if len(source_types) == 1 and source_types:
        parts.append(
            f"All fetched from {list(source_types)[0]}. Consider adding different source types."
        )
        next_tools.append("web_search")

    return (" ".join(parts) if parts else "", next_tools, next_prompts)


def _guide_gemini_search(data: dict) -> tuple[str, list[str], list[str]]:
    del data
    return (GEMINI_QUERY_ADVISORY, [], ["research_methodology"])


def _guide_quick_web_search(data: dict) -> tuple[str, list[str], list[str]]:
    del data
    return ("", ["web_search", "fetch"], [])


def _guide_error(data: dict) -> tuple[str, list[str], list[str]]:
    data = _unwrap_fastmcp_result(data)
    action = data.get("action", "")
    err_type = data.get("error_type", "")
    next_tools: list[str] = []
    if err_type in ("rate_limit_exceeded", "rate_limit", "http_429"):
        next_tools = ["quick_web_search", "gemini_search"]
    elif err_type in ("forbidden", "unauthorized", "auth", "http_401", "http_403"):
        next_tools = ["quick_web_search"]
    elif err_type in ("network", "timeout", "http_502", "http_503", "http_504"):
        next_tools = ["quick_web_search"]
    elif err_type in ("validation_error", "value_error", "content"):
        next_tools = ["web_search", "quick_web_search"]

    if action:
        msg = f"ERROR RECOVERY: {action}"
    elif err_type in ("network", "timeout", "http_502", "http_503", "http_504"):
        msg = "ERROR RECOVERY: Retry once; if persistent use quick_web_search."
    else:
        msg = "Tool Error encountered. Review parameters and API configuration."
    return (msg, next_tools, ["research_methodology"])


GUIDANCE_GENERATORS = {
    "web_search": _guide_web_search,
    "fetch": _guide_fetch,
    "gemini_search": _guide_gemini_search,
    "quick_web_search": _guide_quick_web_search,
}


# ── Middleware ─────────────────────────────────────────────────────────


class DynamicGuidanceMiddleware(Middleware):
    """Per-tool, result-aware guidance — bridges server knowledge to agent.

    Appends agent_guidance, suggested_next_tools, and suggested_prompts
    to tool results based on actual returned data.
    """

    def __init__(self) -> None:
        self._gemini_sessions = SessionTracker(_GEMINI_GUIDANCE_SESSION_TIMEOUT_SECONDS)

    async def on_call_tool(self, context: MiddlewareContext, call_next) -> Any:
        tool_name = context.message.name
        try:
            result = await call_next(context)
        except Exception as exc:
            logger.exception("Error executing tool '%s': %s", tool_name, exc)
            # Prefer the classification attached by raise_tool_error; fall back
            # to re-classifying unexpected exceptions (masking will hide details
            # from clients, but guidance still benefits from the classification).
            structured_error = getattr(exc, "structured", None)
            if structured_error is None:
                structured_error = classify_error(exc, provider=tool_name)
            err_dict = structured_error.to_dict()
            msg, next_tools, next_prompts = _guide_error(err_dict)
            structured = dict(err_dict)
            ag = list(structured.get("agent_guidance") or [])
            ag.append({"source": "error_recovery_guidance", "message": msg.strip()})
            structured["agent_guidance"] = ag
            if next_tools:
                existing_tools = list(structured.get("suggested_next_tools") or [])
                existing_tools.extend(next_tools)
                structured["suggested_next_tools"] = existing_tools
            if next_prompts:
                existing_prompts = list(structured.get("suggested_prompts") or [])
                existing_prompts.extend(next_prompts)
                structured["suggested_prompts"] = existing_prompts
            return ToolResult(
                content=[TextContent(type="text", text=str(exc))],
                structured_content=structured,
                is_error=True,
            )

        if tool_name == "gemini_search":
            session_id = get_session_id(context)
            call_count = self._gemini_sessions.increment(session_id, tool_name)
            if call_count > 2:
                return result

            if isinstance(result, ToolResult) and isinstance(result.structured_content, dict):
                msg, next_tools, next_prompts = _guide_gemini_search(result.structured_content)
                if msg or next_tools or next_prompts:
                    return _append_enrichment(
                        result,
                        "gemini_advisory",
                        msg,
                        next_tools=next_tools,
                        next_prompts=next_prompts,
                    )
            return result

        if isinstance(result, ToolResult) and isinstance(result.structured_content, dict):
            unwrapped = _unwrap_fastmcp_result(result.structured_content)
            if (
                getattr(result, "is_error", False)
                or unwrapped.get("isError") is True
                or ("error" in unwrapped and unwrapped.get("status") == "error")
            ):
                msg, next_tools, next_prompts = _guide_error(unwrapped)
                return _append_enrichment(
                    result,
                    "error_recovery_guidance",
                    msg,
                    next_tools=next_tools,
                    next_prompts=next_prompts,
                )

        generator = GUIDANCE_GENERATORS.get(tool_name)
        if generator is None:
            return result

        if isinstance(result, ToolResult) and isinstance(result.structured_content, dict):
            msg, next_tools, next_prompts = generator(result.structured_content)
            if msg or next_tools or next_prompts:
                return _append_enrichment(
                    result,
                    "dynamic_guidance",
                    msg,
                    next_tools=next_tools,
                    next_prompts=next_prompts,
                )

        return result


def create_dynamic_guidance_middleware() -> DynamicGuidanceMiddleware:
    return DynamicGuidanceMiddleware()


__all__ = [
    "GEMINI_TOOLS",
    "GEMINI_QUERY_ADVISORY",
    "DynamicGuidanceMiddleware",
    "create_dynamic_guidance_middleware",
]
