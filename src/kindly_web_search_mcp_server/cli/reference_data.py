from __future__ import annotations

TOOL_COVERAGE: tuple[dict[str, object], ...] = (
    {
        "tool": "web_search",
        "command": "search web",
        "profiles": ["default", "research", "media", "diagnostic", "experimental", "full"],
        "required": ["query", "research_goal"],
    },
    {
        "tool": "get_content",
        "command": "content get",
        "profiles": ["default", "research", "media", "diagnostic", "experimental", "full"],
        "required": ["url"],
    },
    {
        "tool": "batch_get_content",
        "command": "content batch",
        "profiles": ["default", "research", "media", "diagnostic", "experimental", "full"],
        "required": ["url_or_cursor"],
    },
    {
        "tool": "discover_links",
        "command": "links discover",
        "profiles": ["default", "research", "media", "diagnostic", "experimental", "full"],
        "required": ["url"],
    },
    {
        "tool": "gemini_search",
        "command": "ai gemini",
        "profiles": ["research", "experimental", "full"],
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
        "tool": "code_search",
        "command": "search code",
        "profiles": ["default", "research", "experimental", "full"],
        "required": ["query"],
    },
    {
        "tool": "quick_web_search",
        "command": "search quick",
        "profiles": ["research", "experimental", "full"],
        "required": ["search_query", "objective"],
    },
    {
        "tool": "composio_similarlinks",
        "command": "links similar",
        "profiles": ["research", "experimental", "full"],
        "required": ["url"],
    },
    {
        "tool": "generate_sitemap",
        "command": "sitemap generate",
        "profiles": ["default", "research", "experimental", "full"],
        "required": ["url"],
    },
    {
        "tool": "youtube_search",
        "command": "youtube search",
        "profiles": ["default", "media", "experimental", "full"],
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
    "search web",
    "search quick",
    "search academic",
    "search code",
    "content get",
    "content batch",
    "links discover",
    "links similar",
    "ai gemini",
    "ai grok",
    "youtube search",
    "youtube transcript",
    "analytics query",
    "analytics report",
    "sitemap generate",
    "reference tools",
    "reference external-tools",
    "server start",
)
