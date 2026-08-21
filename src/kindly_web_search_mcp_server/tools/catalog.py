from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from mcp.types import ToolAnnotations


DEFAULT_PROFILE_TOOLS = frozenset(
    {
        "quick_web_search",
        "recommend_command",
        "code_search",
        "code_fetch",
        "web_search",
        "get_content",
        "batch_get_content",
        "gemini_search",
        "generate_sitemap",
        "youtube_search",
        "youtube_transcript",
        "deep_research",
    }
)


# Tool-level timeouts in seconds (None = no timeout enforced by FastMCP)
_TOOL_TIMEOUTS: dict[str, float | None] = {
    "generate_sitemap": 90.0,
    "grok_search": 60.0,
    "web_search": 60.0,
    "batch_get_content": 60.0,
    "get_content": 30.0,
    "academic_search": 45.0,
    "code_search": 120.0,
    "code_fetch": 90.0,
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
    "recommend_command": _entry(
        "recommend_command",
        "Recommend Command",
        {"regular", "full"},
        description=(
            "Recommend an existing CLI/MCP route from a natural-language task. "
            "Returns structured route, fallback, decomposition, and optional prompt metadata; "
            "it is recommendation-only and never executes commands or provider calls."
        ),
        open_world=False,
    ),
    "quick_web_search": _entry("quick_web_search", "Quick Web Search", {"regular", "full"}),
    "web_search": _entry("web_search", "Web Search", {"regular", "full"}, task=True),
    "get_content": _entry("get_content", "Get Content", {"regular", "full"}),
    "batch_get_content": _entry("batch_get_content", "Batch Get Content", {"regular", "full"}),
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
        description=(
            "Search public source code, implementation examples, technical documentation, "
            "and GitHub repositories. Use this for existing implementations, exact "
            "identifiers, API usage patterns, error-message matches, code snippets, or "
            "candidate repositories. Backend selection is automatic across lexical, "
            "symbol, regular-expression, semantic, repository, documentation, and Hugging Face "
            'semantic Hub asset search. Use mode="huggingface" for models/datasets. '
            "Use repositories, language, path, filename, extension, or topic to narrow "
            "the search. Results are grouped by repository (Octocode-style): each group "
            "contains files with text_matches (source windows), match_lines with exact "
            "spans, symbols, sha, and url. Hints and next continuations guide agents to "
            "fetch exact line anchors via get_content. Use web_search or get_content for "
            "general web pages and narrative research."
        ),
        task=True,
    ),
    "code_fetch": _entry(
        "code_fetch",
        "Code Fetch",
        {"regular", "full"},
        description=(
            "Explore a GitHub repository's current main/default branch. Materializes a "
            "five-minute snapshot, then searches, reads, or graphs it. Pass repository plus "
            "optional query, path, or symbol. Do not pass a commit SHA. Successful responses "
            "include resolved_commit and cache_age_seconds."
        ),
    ),
    "composio_similarlinks": _entry(
        "composio_similarlinks", "Composio Similarlinks", {"regular", "full"}
    ),
    "youtube_search": _entry("youtube_search", "YouTube Search", {"regular", "full"}),
    "youtube_transcript": _entry("youtube_transcript", "YouTube Transcript", {"regular", "full"}),
    "youtube_channel_transcription": _entry(
        "youtube_channel_transcription",
        "YouTube Channel Transcription",
        {"regular", "full"},
        description=(
            "Enumerate a channel uploads playlist and transcribe its videos with "
            "cache-first processing, always-on GLiNER2 extraction, optional Gemini "
            "summaries, partial failure reporting, and FastMCP background-task support."
        ),
        expensive=True,
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
        description=(
            "Autonomous multi-step web research via the self-hosted node-DeepResearch "
            "engine. Runs as a background task (SEP-1686) for long investigations. "
            "Use for multi-source technical investigations, SDK/library comparisons, "
            "architectural trade-off analysis, or obscure bug fixes across docs and "
            "forums. Not for local codebase searches or single-fact questions."
        ),
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
