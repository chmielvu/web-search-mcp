from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp.types import ToolAnnotations


DEFAULT_PROFILE_TOOLS = frozenset(
    {"web_search", "get_content", "batch_get_content", "discover_links"}
)


@dataclass(frozen=True)
class ToolCatalogEntry:
    name: str
    title: str
    profiles: frozenset[str]
    tags: frozenset[str]
    annotations: ToolAnnotations
    expensive: bool = False
    experimental: bool = False


def _entry(
    name: str,
    title: str,
    profiles: set[str],
    *,
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
        annotations=ToolAnnotations(
            title=title,
            readOnlyHint=True,
            idempotentHint=idempotent,
            openWorldHint=True,
        ),
        expensive=expensive,
        experimental=experimental,
    )


TOOL_CATALOG: dict[str, ToolCatalogEntry] = {
    "web_search": _entry(
        "web_search",
        "Web Search",
        {"default", "research", "media", "diagnostic", "experimental", "full"},
    ),
    "get_content": _entry(
        "get_content",
        "Get Content",
        {"default", "research", "media", "diagnostic", "experimental", "full"},
    ),
    "batch_get_content": _entry(
        "batch_get_content",
        "Batch Get Content",
        {"default", "research", "media", "diagnostic", "experimental", "full"},
    ),
    "discover_links": _entry(
        "discover_links",
        "Discover Links",
        {"default", "research", "media", "diagnostic", "experimental", "full"},
    ),
    "gemini_search": _entry(
        "gemini_search", "Gemini Search", {"research", "experimental", "full"}
    ),
    "perplexity_search": _entry(
        "perplexity_search",
        "Perplexity Search",
        {"research", "experimental", "full"},
        expensive=True,
    ),
    "grok_search": _entry(
        "grok_search",
        "Grok Search",
        {"research", "experimental", "full"},
        expensive=True,
        idempotent=False,
    ),
    "academic_search": _entry(
        "academic_search", "Academic Search", {"research", "experimental", "full"}
    ),
    "agentic_web_research": _entry(
        "agentic_web_research",
        "Agentic Web Research",
        {"research", "experimental", "full"},
        experimental=True,
        idempotent=False,
    ),
    "youtube_search": _entry(
        "youtube_search", "YouTube Search", {"media", "experimental", "full"}
    ),
    "youtube_transcript": _entry(
        "youtube_transcript", "YouTube Transcript", {"media", "experimental", "full"}
    ),
}


def catalog_entry(tool_name: str) -> ToolCatalogEntry:
    return TOOL_CATALOG[tool_name]


def tool_kwargs(tool_name: str) -> dict[str, Any]:
    entry = catalog_entry(tool_name)
    return {"tags": entry.tags, "annotations": entry.annotations}
