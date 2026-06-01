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
from fastmcp.server.server import ToolResult

from .session_tracking import SessionTracker, get_session_id

logger = logging.getLogger(__name__)
GEMINI_TOOLS = frozenset({"gemini_search"})
GEMINI_QUERY_ADVISORY = """
GEMINI SEARCH: Best for quick grounded synthesis. Use a single focused question, include exact API/error/version terms, and add recency hints when freshness matters. Use web_search plus get_content when you need to compare source pages yourself.
"""
_GEMINI_GUIDANCE_SESSION_TIMEOUT_SECONDS = 300


# ── Helpers ────────────────────────────────────────────────────────────


def _append_enrichment(
    result: Any,
    source: str,
    message: str,
    next_tools: list[str] | None = None,
    next_prompts: list[str] | None = None,
) -> Any:
    """Attach guidance + suggested_next_tools + suggested_prompts to ToolResult."""
    if not isinstance(result, ToolResult) or not isinstance(
        result.structured_content, dict
    ):
        return result

    structured = dict(result.structured_content)
    ag = list(structured.get("agent_guidance") or [])
    ag.append({"source": source, "message": message.strip()})
    structured["agent_guidance"] = ag
    if next_tools:
        structured.setdefault("suggested_next_tools", []).extend(next_tools)
    if next_prompts:
        structured.setdefault("suggested_prompts", []).extend(next_prompts)
    return ToolResult(structured_content=structured, meta=result.meta)


def _extract_domain(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else ""


def _has_domain(urls: list[str], pattern: str) -> bool:
    return any(re.search(pattern, u) for u in urls)


# ── Per-tool guidance generators ──────────────────────────────────────


def _guide_web_search(data: dict) -> tuple[str, list[str], list[str]]:
    results = data.get("results", [])
    providers = data.get("providers_used", [])
    urls = [r.get("link", "") for r in results]
    next_tools: list[str] = []
    next_prompts: list[str] = ["evaluate_web_results"]
    parts: list[str] = []

    if not results:
        return (
            "Zero results. Broaden: remove specific terms, set rewrite=true, "
            "or try gemini_search for a grounded answer without URLs.",
            ["gemini_search"],
            next_prompts,
        )

    parts.append(f"{len(results)} results from {len(providers)} providers.")

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
        parts.append(
            f"All results from {list(domains)[0]}. "
            "Try gemini_search for broader coverage."
        )
        next_tools.append("gemini_search")

    # Provider agreement
    top_pc = max((r.get("provider_count", 0) for r in results[:3]), default=0)
    if top_pc <= 1 and len(providers) > 1:
        parts.append(
            "Top results from single provider — cross-check with gemini_search."
        )
        next_tools.append("gemini_search")

    return (" ".join(parts), next_tools, next_prompts)


def _guide_get_content(data: dict) -> tuple[str, list[str], list[str]]:
    parts: list[str] = []
    next_tools: list[str] = []
    next_prompts: list[str] = ["evaluate_web_results"]
    source_type = data.get("source_type", "")
    fetch_backend = data.get("fetch_backend", "")
    window = data.get("window", {})
    status = data.get("status", "")
    content_len = len(data.get("page_content", ""))

    if window.get("has_more"):
        nxt = window.get("next_offset", 0)
        parts.append(
            f"Truncated at {nxt} chars. Continue: get_content(char_offset={nxt})."
        )
        next_tools.append("get_content")

    if source_type == "github_issue":
        parts.append(
            "GitHub issue detected. "
            "Use composio_similarlinks to find related issues/PRs."
        )
        next_tools.append("composio_similarlinks")
        next_prompts.append("research_gap_analysis")
    elif source_type == "wikipedia":
        parts.append(
            "Wikipedia source. Cross-reference with academic_search or official docs."
        )
        next_tools.append("academic_search")

    if fetch_backend == "browser_fallback":
        parts.append(
            "Used browser fallback (JS-heavy page). Content may be less complete."
        )

    if content_len < 300 and not window.get("has_more") and status != "error":
        parts.append(
            "Very short content (possibly behind login/paywall). Try alternative source."
        )

    return (" ".join(parts) if parts else "", next_tools, next_prompts)


def _guide_batch_get_content(data: dict) -> tuple[str, list[str], list[str]]:
    parts: list[str] = []
    next_tools: list[str] = []
    next_prompts: list[str] = ["research_gap_analysis"]
    results = data.get("results", [])
    has_more = data.get("has_more", False)
    cursor = data.get("cursor")
    total_req = data.get("total_requested", 0)

    if has_more and cursor:
        remaining = total_req - len(results)
        parts.append(
            f"has_more=true ({remaining} URLs pending). "
            f"Continue: batch_get_content(cursor={cursor})."
        )
        next_tools.append("batch_get_content")

    success_count = sum(1 for r in results if r.get("status") == "success")
    if total_req > 0 and success_count < total_req:
        parts.append(f"{success_count}/{total_req} URLs succeeded.")

    source_types = {
        r.get("source_type", "") for r in results if r.get("status") == "success"
    }
    if len(source_types) == 1 and source_types:
        parts.append(
            f"All fetched from {list(source_types)[0]}. "
            "Consider adding different source types."
        )
        next_tools.append("web_search")

    return (" ".join(parts) if parts else "", next_tools, next_prompts)


def _guide_gemini_search(data: dict) -> tuple[str, list[str], list[str]]:
    del data
    return (GEMINI_QUERY_ADVISORY, [], ["evaluate_web_results"])


GUIDANCE_GENERATORS = {
    "web_search": _guide_web_search,
    "get_content": _guide_get_content,
    "batch_get_content": _guide_batch_get_content,
    "gemini_search": _guide_gemini_search,
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
        result = await call_next(context)

        if tool_name == "gemini_search":
            session_id = get_session_id(context)
            call_count = self._gemini_sessions.increment(session_id, tool_name)
            if call_count > 2:
                return result

            if isinstance(result, ToolResult) and isinstance(
                result.structured_content, dict
            ):
                msg, next_tools, next_prompts = _guide_gemini_search(
                    result.structured_content
                )
                if msg or next_tools or next_prompts:
                    return _append_enrichment(
                        result,
                        "gemini_advisory",
                        msg,
                        next_tools=next_tools,
                        next_prompts=next_prompts,
                    )
            return result

        generator = GUIDANCE_GENERATORS.get(tool_name)
        if generator is None:
            return result

        if isinstance(result, ToolResult) and isinstance(
            result.structured_content, dict
        ):
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
