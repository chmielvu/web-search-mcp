from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from mcp.types import ToolAnnotations


DEFAULT_PROFILE_TOOLS = frozenset(
    {
        "quick_web_search",
        "code_search",
        "code_fetch",
        "web_search",
        "fetch",
        "gemini_search",
        "generate_sitemap",
        "youtube_transcript",
        "deep_research",
    }
)


# Tool-level timeouts in seconds (None = no timeout enforced by FastMCP)
_TOOL_TIMEOUTS: dict[str, float | None] = {
    "generate_sitemap": 90.0,
    "grok_search": 60.0,
    "web_search": 120.0,
    "fetch": 120.0,
    "academic_search": 45.0,
    "code_search": 120.0,
    "code_fetch": 180.0,
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
            readOnlyHint=read_only,
            idempotentHint=idempotent,
            openWorldHint=open_world,
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
        version="2.0",
        task=True,
    ),
    "fetch": _entry("fetch", "Fetch", {"regular", "full"}),
    "gemini_search": _entry("gemini_search", "Gemini Search", {"regular", "full"}),
    "grok_search": _entry(
        "grok_search",
        "Grok Search",
        {"full"},
        expensive=True,
        idempotent=False,
    ),
    "academic_search": _entry("academic_search", "Academic Search", {"regular", "full"}),
    "code_search": _entry(
        "code_search",
        "Code Search & Repository Discovery",
        {"regular", "full"},
        task=True,
    ),
    # Hidden from MCP clients via tools.profiles.DISABLED_TOOLS (visibility
    # disable after profile selection); kept registered so CLI/service imports
    # and catalog metadata stay stable.
    "code_fetch": _entry("code_fetch", "Code Fetch", {"regular", "full"}),
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
        {"regular", "research", "full"},
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
        from fastmcp.server.tasks import TaskConfig

        kwargs["task"] = TaskConfig(
            mode="optional",
            poll_interval=timedelta(seconds=entry.task_poll_interval_seconds),
        )
    return kwargs
