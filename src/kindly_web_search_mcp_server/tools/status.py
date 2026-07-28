from __future__ import annotations

import os

from ..settings import settings


def get_providers_status() -> str:
    """Report which search providers are configured."""
    lines = [
        "# Search Provider Status",
        "",
        f"**SearXNG** (Primary): {'✓ Configured' if os.environ.get('SEARXNG_BASE_URL') else '✗ Not configured'}",
        f"**Tavily**: {'✓ Configured' if os.environ.get('TAVILY_API_KEY') else '✗ Not configured'}",
        f"**Brave**: {'✓ Configured' if os.environ.get('BRAVE_API_KEY') else '✗ Not configured'}",
        f"**Search Router**: {'✓ Configured' if os.environ.get('SEARCH_ROUTER_API_KEY') else '✗ Not configured'}",
        f"**Jina**: {'✓ Configured' if os.environ.get('JINA_API_KEY') else '✗ Not configured'}",
        f"**LangSearch**: {'✓ Configured' if os.environ.get('LANGSEARCH_API_KEY') else '✗ Not configured'}",
        f"**Cohere Reranker**: {'✓ Configured' if settings.cohere_api_key else '✗ Not configured'}",
        f"**OpenRouter Reranker**: {'✓ Configured' if settings.openrouter_api_key else '✗ Not configured'}",
        f"**Voyage Reranker**: {'✓ Configured' if settings.voyage_api_key else '✗ Not configured'}",
        f"**Composio LLM Search**: {'✓ Configured' if os.environ.get('COMPOSIO_API_KEY') and os.environ.get('COMPOSIO_USER_ID') else '✗ Not configured'}",
        "",
        "## AI Search",
        f"**Gemini**: {'✓ Configured' if settings.gemini_api_key else '✗ Not configured'}",
        f"**Gemma SERP / Pollinations**: {'✓ Configured' if os.environ.get('POLLINATIONS_API_KEY') else '✗ Not configured'}",
        "",
        "## Academic Search",
        f"**Semantic Scholar**: ✓ Always available (API key optional: {'set' if os.environ.get('S2_API_KEY', '').strip() else 'not set — shared rate limit'})",
        "**ArXiv**: ✓ Always available (no auth required)",
        "",
        "## Other",
        f"**GitHub Token**: {'✓ Configured' if os.environ.get('GITHUB_TOKEN') else '✗ Not configured'}",
    ]
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
        f"**Reranking**: {'✓ Enabled' if settings.rerank_entity_overlap_enabled else '✗ Disabled'}",
        "## Cache Settings",
        "Page cache: DuckDB (separate file)",
        "",
        "## Timeouts",
        f"Tool Timeout: {os.environ.get('TOOL_TOTAL_TIMEOUT_SECONDS', '120')}s",
        f"YouTube Transcript Timeout: {settings.youtube_transcript_timeout_seconds}s",
    ]
    return "\n".join(lines)
