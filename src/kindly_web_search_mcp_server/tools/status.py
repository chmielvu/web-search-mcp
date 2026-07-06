from __future__ import annotations

import os

from ..search.provider_health import get_provider_health
from ..settings import settings


def get_providers_status() -> str:
    """Which search providers are configured and their health state."""
    lines = [
        "# Search Provider Status",
        "",
        f"**SearXNG** (Primary): {'✓ Configured' if os.environ.get('SEARXNG_BASE_URL') else '✗ Not configured'}",
        f"**Tavily**: {'✓ Configured' if os.environ.get('TAVILY_API_KEY') else '✗ Not configured'}",
        f"**Brave**: {'✓ Configured' if os.environ.get('BRAVE_API_KEY') else '✗ Not configured'}",
        f"**Search Router**: {'✓ Configured' if os.environ.get('SEARCH_ROUTER_API_KEY') else '✗ Not configured'}",
        f"**Jina**: {'✓ Configured' if os.environ.get('JINA_API_KEY') else '✗ Not configured'}",
        f"**Cohere Reranker**: {'✓ Configured' if settings.cohere_api_key else '✗ Not configured'}",
        f"**OpenRouter Reranker**: {'✓ Configured' if settings.openrouter_api_key else '✗ Not configured'}",
        f"**Voyage Reranker**: {'✓ Configured' if settings.voyage_api_key else '✗ Not configured'}",
        f"**Composio LLM Search**: {'✓ Configured' if os.environ.get('COMPOSIO_API_KEY') and os.environ.get('COMPOSIO_USER_ID') else '✗ Not configured'}",
        "",
        "## AI Search",
        f"**Gemini**: {'✓ Configured' if settings.gemini_api_key else '✗ Not configured'}",
        "**Gemma 4**: ✓ Free provider (always available)",
        "",
        "## Academic Search",
        f"**Semantic Scholar**: ✓ Always available (API key optional: {'set' if os.environ.get('S2_API_KEY', '').strip() else 'not set — shared rate limit'})",
        "**ArXiv**: ✓ Always available (no auth required)",
        "",
        "## Other",
        f"**GitHub Token**: {'✓ Configured' if os.environ.get('GITHUB_TOKEN') else '✗ Not configured'}",
        "",
        "## Provider Health",
    ]

    tracker = get_provider_health()
    for state in tracker.all_states():
        if state["cooldown_remaining_s"] > 0:
            lines.append(
                f"- **{state['provider']}**: ⚠️ IN COOLDOWN ({state['cooldown_remaining_s']}s remaining) — "
                f"{state['consecutive_failures']} consecutive failures"
            )
        elif state["total_failures"] > 0:
            lines.append(
                f"- **{state['provider']}**: ✓ healthy — "
                f"{state['total_successes']} successes, {state['total_failures']} failures"
            )

    if not tracker.all_states():
        lines.append("- No providers have been called yet.")

    return "\n".join(lines)


def get_features_status() -> str:
    """Server feature flags status."""
    lines = [
        "# Feature Status",
        "",
        "## Personal Enhanced Profile",
        f"**Current Tool Profile**: {settings.tool_profile}",
        f"**Entity Extraction**: {'✓ Enabled' if settings.entity_extraction_enabled else '✗ Disabled'}",
        f"**Entity Overlap Rerank**: {'✓ Enabled' if settings.rerank_entity_overlap_enabled else '✗ Disabled'}",
        f"**Result Memory**: {'✓ Enabled' if settings.web_results_index_enabled else '✗ Disabled'}",
        "",
        f"**Reranking**: {'✓ Enabled' if settings.reranking_enabled else '✗ Disabled'}",
        "",
        "## Cache Settings",
        "Page cache: DuckDB (separate file)",
        "",
        "## Timeouts",
        f"Tool Timeout: {os.environ.get('TOOL_TOTAL_TIMEOUT_SECONDS', '120')}s",
        f"YouTube Transcript Timeout: {settings.youtube_transcript_timeout_seconds}s",
    ]
    return "\n".join(lines)
