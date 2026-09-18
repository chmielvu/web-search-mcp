from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastmcp.utilities.tasks import TaskConfig
from mcp.types import ToolAnnotations

DEFAULT_PROFILE_TOOLS = frozenset(
    {
        "quick_web_search",
        "web_search",
        "fetch",
        "crawl_web",
        "gemini_search",
        "generate_sitemap",
        "youtube_transcript",
        "deep_research",
    }
)


# Tool-level timeouts in seconds (None = no timeout enforced by FastMCP).
# ``fetch`` / ``crawl_web`` must cover Jina + Crawl4AI + Camoufox + Unlocker
# (see ``fetch_deadline_seconds``); 120s kills Unlocker after a slow Crawl4AI.
_TOOL_TIMEOUTS: dict[str, float | None] = {
    "generate_sitemap": 90.0,
    "grok_search": 60.0,
    # web_search: None — the bounded three-wave adaptive run must not be
    # killed by a one-pass tool-wide timeout; provider/reranker/LLM call
    # timeouts and caller cancellation stay operative.
    "web_search": None,
    "fetch": 240.0,
    "crawl_web": 240.0,
    "academic_search": 45.0,
    "deep_research": None,  # background-capable; no foreground timeout
}


def _tool_timeout(name: str) -> float | None:
    return _TOOL_TIMEOUTS.get(name)


@dataclass(frozen=True)
class ToolCatalogEntry:
    name: str
    title: str
    profiles: frozenset[str]
    tags: frozenset[str]
    description: str = ""
    read_only: bool = True
    idempotent: bool = True
    open_world: bool = True
    expensive: bool = False
    experimental: bool = False
    annotations: ToolAnnotations | None = None
    version: str = "1.0"
    timeout: float | None = None
    task: bool = False
    task_poll_interval_seconds: float = 5.0


def _entry(
    name: str,
    title: str,
    profiles: set[str],
    *,
    description: str = "",
    read_only: bool = True,
    open_world: bool = True,
    expensive: bool = False,
    experimental: bool = False,
    idempotent: bool = True,
    version: str = "1.0",
    task: bool = False,
    task_poll_interval_seconds: float = 5.0,
) -> ToolCatalogEntry:
    tags = {"tool:public", f"tool:{name}", *(f"profile:{p}" for p in profiles)}
    if expensive:
        tags.add("tool:expensive")
    if experimental:
        tags.add("tool:experimental")
    return ToolCatalogEntry(
        name=name,
        title=title,
        profiles=frozenset(profiles),
        tags=frozenset(tags),
        description=description,
        read_only=read_only,
        idempotent=idempotent,
        open_world=open_world,
        expensive=expensive,
        experimental=experimental,
        annotations=ToolAnnotations(
            title=title,
            read_only_hint=read_only,
            idempotent_hint=idempotent,
            open_world_hint=open_world,
        ),
        version=version,
        timeout=_tool_timeout(name),
        task=task,
        task_poll_interval_seconds=task_poll_interval_seconds,
    )


TOOL_CATALOG: dict[str, ToolCatalogEntry] = {
    "quick_web_search": _entry("quick_web_search", "Quick Web Search", {"regular", "full"}),
    "web_search": _entry(
        "web_search",
        "Web Search",
        {"regular", "full"},
        version="4.0",
        task=True,
    ),
    "fetch": _entry("fetch", "Fetch", {"regular", "full"}),
    "crawl_web": _entry(
        "crawl_web",
        "Crawl Web",
        {"regular", "full"},
        description=(
            "Crawl one or more public HTTP(S) seed URLs through Crawl4AI with bounded "
            "breadth-first traversal. Use web-search:crawl_web for multi-page site traversal "
            "or browser-rendered pages; use web-search:fetch for one or more known URLs without "
            "link traversal. Pass request={urls:[...], max_depth:0..2, max_pages:1..100, "
            "include_external:false, targets?:{css_selector, allowed_domains, excluded_domains, "
            "include_patterns, exclude_patterns}, interaction?:{javascript_before_wait, wait_for, "
            "javascript, scan_full_page}, response_format:'summary'|'detailed'}. Summary returns "
            "status, depth, word and Markdown-structure counts, diagnostics, and deterministic "
            "output_path without page Markdown; detailed also returns content, links, and compact "
            "Crawl4AI capability evidence. Per-page failures return typed error objects with "
            "resolution and retryable fields."
        ),
        read_only=False,
        idempotent=True,
        open_world=True,
    ),
    "gemini_search": _entry("gemini_search", "Gemini Search", {"regular", "full"}),
    "grok_search": _entry(
        "grok_search",
        "Grok Search",
        {"full"},
        expensive=True,
        idempotent=False,
    ),
    "academic_search": _entry("academic_search", "Academic Search", {"regular", "full"}),
    "composio_similarlinks": _entry(
        "composio_similarlinks", "Composio Similarlinks", {"regular", "full"}
    ),
    "youtube_transcript": _entry(
        "youtube_transcript",
        "YouTube Transcript",
        {"regular", "full"},
        idempotent=False,
        task=True,
        task_poll_interval_seconds=30.0,
    ),
    "generate_sitemap": _entry(
        "generate_sitemap",
        "Generate Sitemap",
        {"regular", "full"},
        expensive=True,
        task=True,
    ),
    "deep_research": _entry(
        "deep_research",
        "Deep Research",
        {"regular", "full"},
        expensive=True,
        idempotent=False,
        task=True,
        task_poll_interval_seconds=30.0,
    ),
}


def catalog_entry(tool_name: str) -> ToolCatalogEntry:
    return TOOL_CATALOG[tool_name]


def tool_kwargs(tool_name: str) -> dict[str, Any]:
    entry = catalog_entry(tool_name)
    if entry.annotations is None:
        raise ValueError(f"tool catalog entry {tool_name!r} is missing annotations")
    kwargs: dict[str, Any] = {
        "tags": entry.tags,
        "annotations": entry.annotations,
        "version": entry.version,
        "title": entry.title,
    }
    if entry.description:
        kwargs["description"] = entry.description
    if entry.timeout is not None:
        kwargs["timeout"] = entry.timeout
    if entry.task:
        kwargs["task"] = TaskConfig(
            mode="optional",
            poll_interval=timedelta(seconds=entry.task_poll_interval_seconds),
        )
    return kwargs
