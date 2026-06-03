"""Default entity label schemas for tech/web/coding content.

Descriptions are critical for GLiNER2 zero-shot quality (per GLiNER papers).
These are used by query (light) and content (richer) extraction paths.
"""

from __future__ import annotations

# Core labels for queries (precision literals that must survive rewrite)
DEFAULT_QUERY_LABELS: dict[str, str] = {
    "package": "Software package, library, or framework name (e.g. FastMCP, React, numpy, pydantic)",
    "version": "Software version string (e.g. 2.14.5, v3.0.0, 1.0.0-beta)",
    "api_function": "API endpoint, function, or method name (e.g. FastMCP.tool, requests.get, useState)",
    "error_class": "Error or exception class name (e.g. ImportError, TypeError, HTTPStatusError)",
    "repo_ref": "GitHub/GitLab repository reference (e.g. owner/repo, owner/repo#123)",
    "cli_flag": "Command-line flag or argument (e.g. --verbose, -rf, --port 8000)",
    "model_id": "ML model identifier (e.g. bert-base-uncased, gpt-4o, voyage-3)",
    "file_path": "File path or module path (e.g. src/app.ts, kindly_web_search_mcp_server.server)",
    "env_var": "Environment variable name (e.g. SEARXNG_BASE_URL, KINDLY_RERANKING_ENABLED)",
}

# Richer labels for fetched content (adds general web entities)
DEFAULT_CONTENT_LABELS: dict[str, str] = {
    **DEFAULT_QUERY_LABELS,
    "person": "Person name (developer, author, maintainer)",
    "organization": "Company, team, or organization (e.g. Microsoft, Fastino AI, Hugging Face)",
    "date": "Date or time expression (e.g. 2025-06-03, last week, June 2025)",
    "product": "Product name (e.g. iPhone 15, Azure OpenAI, Cloud Run)",
    "url": "URL or web address",
}
