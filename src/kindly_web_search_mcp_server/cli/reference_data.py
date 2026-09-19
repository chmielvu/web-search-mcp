from __future__ import annotations

from typing import TypedDict


class ToolCoverageEntry(TypedDict, total=False):
    """One MCP tool and the CLI command that reaches it.

    ``purpose`` is present on the entries that carry a longer explanation.
    """

    tool: str
    command: str
    profiles: list[str]
    required: list[str]
    purpose: str


_ALL_PROFILES: list[str] = [
    "default",
    "research",
    "media",
    "diagnostic",
    "experimental",
    "full",
]

# NOTE: ``profiles`` below is the CLI reference grouping consumed by
# ``reference tools --profile``. It is intentionally distinct from the MCP
# ``tool_profile`` setting (``regular`` | ``full`` in tools/profiles.py):
# it answers "which CLI audience needs this command", not "which MCP
# clients see this tool". Keep every one of the six values present on at
# least one entry so no ``--profile`` value ever returns zero rows.

TOOL_COVERAGE: tuple[ToolCoverageEntry, ...] = (
    {
        "tool": "web_search",
        "command": "search web",
        "profiles": list(_ALL_PROFILES),
        "required": ["query", "research_goal"],
    },
    {
        "tool": "fetch",
        "command": "content fetch",
        "profiles": list(_ALL_PROFILES),
        "required": ["url_or_urls_or_cursor"],
    },
    {
        "tool": "generate_sitemap",
        "command": "sitemap generate",
        "profiles": ["default", "research", "experimental", "full"],
        "required": ["url"],
    },
    {
        "tool": "gemini_search",
        "command": "ai gemini",
        "profiles": ["default", "research", "experimental", "full"],
        "required": ["query"],
    },
    {
        "tool": "grok_search",
        "command": "ai grok",
        "profiles": ["research", "experimental", "full"],
        "required": ["query", "research_goal"],
    },
    {
        "tool": "academic_search",
        "command": "search academic",
        "profiles": ["research", "experimental", "full"],
        "required": ["query"],
    },
    {
        "tool": "quick_web_search",
        "command": "search quick",
        "profiles": ["default", "research", "experimental", "full"],
        "required": ["search_query", "objective"],
    },
    {
        "tool": "composio_similarlinks",
        "command": "links similar",
        "profiles": ["research", "experimental", "full"],
        "required": ["url"],
    },
    {
        "tool": "crawl_web",
        "command": "content crawl",
        "profiles": list(_ALL_PROFILES),
        "required": ["urls"],
    },
    {
        "tool": "deep_research",
        "command": "research deep",
        "profiles": ["default", "research", "experimental", "full"],
        "required": ["query"],
    },
    {
        "tool": "youtube_transcript",
        "command": "youtube transcript",
        "profiles": ["default", "media", "experimental", "full"],
        "required": ["video_id_or_url"],
    },
)

EXTERNAL_TOOLS: tuple[dict[str, str], ...] = (
    {
        "tool": "DuckDB",
        "command": "duckdb",
        "purpose": "Inspect local analytics DuckDB files directly.",
    },
    {
        "tool": "Grafana",
        "command": "wsl gcx",
        "purpose": "Use the existing WSL Grafana CLI path for dashboard and cloud context work.",
    },
    {
        "tool": "Phoenix",
        "command": "arize-phoenix",
        "purpose": "Use Phoenix CLI for local dev tracing. Point OTEL_EXPORTER_OTLP_ENDPOINT to your Phoenix instance.",
    },
)

COMMANDS: tuple[str, ...] = (
    "schema",
    "doctor",
    "getskill",
    "skills",
    "server",
    "links discover",
    "links similar",
    "search quick",
    "search web",
    "search inspect",
    "search postmortem",
    "search academic",
    "content fetch",
    "content crawl",
    "ai gemini",
    "ai grok",
    "youtube transcript",
    "youtube channel",
    "analytics query",
    "analytics report",
    "sitemap generate",
    "research deep",
    "research collect",
    "jobs list",
    "jobs get",
    "jobs wait",
    "jobs cancel",
    "jobs resume",
    "results search",
    "inference describe",
    "inference validate",
    "inference chain",
    "feedback create",
    "feedback list",
    "feedback show",
    "feedback close",
    "feedback transition",
    "reference tools",
    "reference external-tools",
    "server start",
)
