from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp.types import ToolAnnotations


DEFAULT_PROFILE_TOOLS = frozenset(
    {
        "quick_web_search",
        "web_search",
        "get_content",
        "batch_get_content",
        "discover_links",
        "gemini_search",
        "generate_semantic_sitemap",
        "youtube_search",
        "youtube_transcript",
    }
)


@dataclass(frozen=True)
class ToolCatalogEntry:
    name: str
    title: str
    profiles: frozenset[str]
    tags: frozenset[str]
    read_only: bool = True
    idempotent: bool = True
    open_world: bool = True
    expensive: bool = False
    experimental: bool = False
    annotations: ToolAnnotations | None = None


def _entry(
    name: str,
    title: str,
    profiles: set[str],
    *,
    read_only: bool = True,
    open_world: bool = True,
    expensive: bool = False,
    experimental: bool = False,
    idempotent: bool = True,
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
    )


TOOL_CATALOG: dict[str, ToolCatalogEntry] = {
    "quick_web_search": _entry(
        "quick_web_search", "Quick Web Search", {"regular", "full"}
    ),
    "web_search": _entry(
        "web_search", "Web Search", {"regular", "full"}
    ),
    "get_content": _entry(
        "get_content", "Get Content", {"regular", "full"}
    ),
    "batch_get_content": _entry(
        "batch_get_content", "Batch Get Content", {"regular", "full"}
    ),
    "discover_links": _entry(
        "discover_links", "Discover Links", {"regular", "full"}
    ),
    "gemini_search": _entry(
        "gemini_search", "Gemini Search", {"regular", "full"}
    ),
    "grok_search": _entry(
        "grok_search",
        "Grok Search",
        {"full"},
        expensive=True,
        idempotent=False,
    ),
    "academic_search": _entry(
        "academic_search", "Academic Search", {"full"}
    ),
    "composio_similarlinks": _entry(
        "composio_similarlinks", "Composio Similarlinks", {"full"}
    ),
    "agentic_web_research": _entry(
        "agentic_web_research",
        "Agentic Web Research",
        {"full"},
        experimental=True,
        idempotent=False,
    ),
    "youtube_search": _entry(
        "youtube_search", "YouTube Search", {"regular", "full"}
    ),
    "youtube_transcript": _entry(
        "youtube_transcript", "YouTube Transcript", {"regular", "full"}
    ),
    "generate_semantic_sitemap": _entry(
        "generate_semantic_sitemap",
        "Generate Semantic Sitemap",
        {"regular", "research", "full"},
        expensive=True,
    ),
}


def catalog_entry(tool_name: str) -> ToolCatalogEntry:
    return TOOL_CATALOG[tool_name]


def tool_kwargs(tool_name: str) -> dict[str, Any]:
    entry = catalog_entry(tool_name)
    if entry.annotations is None:
        raise ValueError(f"tool catalog entry {tool_name!r} is missing annotations")
    return {"tags": entry.tags, "annotations": entry.annotations}
