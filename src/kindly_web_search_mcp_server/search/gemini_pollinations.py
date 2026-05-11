"""Gemini search provider via Pollinations API for web_search provider mix.

Replaces the old Gemini provider that used Google GenAI SDK directly.
This version uses Pollinations' gemini-search model and preserves grounding metadata.
"""

from __future__ import annotations

import logging
import re
import asyncio
from typing import Any
from urllib.parse import urlparse

import httpx

from ..models import WebSearchResult
from .pollinations import gemini_grounding_search

logger = logging.getLogger(__name__)

# Redirect URL resolution (from old gemini_provider_grounding.py)
_MAX_SNIPPET_CHARS = 240
_TITLE_TAG_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _normalize_snippet(text: str) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= _MAX_SNIPPET_CHARS:
        return cleaned
    return cleaned[: _MAX_SNIPPET_CHARS - 1].rstrip() + "…"


def _is_generic_title(title: str) -> bool:
    normalized = title.strip().lower()
    if not normalized:
        return True
    if " " not in normalized:
        return True
    hostname = normalized.removeprefix("www.")
    return "." in hostname and hostname.count(" ") == 0


def _extract_html_title(html: str) -> str | None:
    match = _TITLE_TAG_RE.search(html)
    if not match:
        return None
    title = " ".join(match.group(1).split()).strip()
    return title or None


async def _resolve_redirect_url(url: str) -> str:
    """Resolve vertexaisearch.cloud.google.com redirect URLs to canonical URLs."""
    parsed = urlparse(url)
    if parsed.netloc != "vertexaisearch.cloud.google.com":
        return url

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            response = await client.get(url)
        return str(response.url)
    except Exception:
        return url


async def _resolve_redirect_result(result: WebSearchResult) -> WebSearchResult:
    """Resolve redirect URL and optionally extract title from HTML."""
    resolved_url = await _resolve_redirect_url(result.link)
    if resolved_url == result.link:
        return result

    updated: dict[str, str] = {"link": resolved_url}

    # Try to extract a better title from the resolved page
    if _is_generic_title(result.title):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(resolved_url)
                html_title = _extract_html_title(response.text)
                if html_title:
                    updated["title"] = html_title
        except Exception:
            pass

    return result.model_copy(update=updated)


def _build_snippets_from_supports(
    grounding_chunks: list[dict[str, Any]],
    grounding_supports: list[dict[str, Any]],
) -> dict[int, str]:
    """Build snippets by mapping groundingSupports segments to chunk indices."""
    from collections import defaultdict

    grouped_segments: dict[int, list[str]] = defaultdict(list)
    for support in grounding_supports:
        text = support.get("text", "")
        if not text.strip():
            continue
        chunk_indices = support.get("chunk_indices", [])
        for idx in chunk_indices:
            if text not in grouped_segments[idx]:
                grouped_segments[idx].append(text)

    return {
        idx: _normalize_snippet(" ".join(segments))
        for idx, segments in grouped_segments.items()
        if segments
    }


async def search_gemini_pollinations(
    query: str,
    *,
    num_results: int,
    http_client: Any = None,  # Not used, Pollinations has its own client
) -> list[WebSearchResult]:
    """Search via Pollinations gemini-search and return results with grounding metadata.

    Focus on groundingChunks as primary source list, NOT synthesized answer.
    Preserves rich grounding metadata in WebSearchResult.diagnostics field.

    Args:
        query: Search query
        num_results: Target number of results
        http_client: Ignored (Pollinations uses its own client)

    Returns:
        list[WebSearchResult] with grounding_chunk metadata in diagnostics
    """
    if not query.strip():
        return []

    if num_results < 1:
        return []

    try:
        response = await gemini_grounding_search(query, num_results=num_results)
    except Exception as e:
        logger.warning(f"gemini-search provider failed: {e}")
        return []

    grounding_metadata = response.get("groundingMetadata", {})
    grounding_chunks = grounding_metadata.get("groundingChunks", [])
    grounding_supports = grounding_metadata.get("groundingSupports", [])
    web_search_queries = grounding_metadata.get("webSearchQueries", [])

    if not grounding_chunks:
        logger.debug(f"gemini-search returned no grounding chunks for: {query}")
        return []

    # Build snippets from groundingSupports
    support_snippets = _build_snippets_from_supports(grounding_chunks, grounding_supports)

    results: list[WebSearchResult] = []
    for idx, chunk in enumerate(grounding_chunks):
        uri = chunk.get("uri")
        title = chunk.get("title")
        domain = chunk.get("domain")

        if not uri or not title:
            continue

        # Get snippet from groundingSupports if available
        snippet = support_snippets.get(idx, "")

        result = WebSearchResult(
            title=title,
            link=uri,
            snippet=snippet,
            domain=domain,
            providers=["gemini"],
            diagnostics=[
                {
                    "grounding_chunk": {
                        "uri": uri,
                        "title": title,
                        "domain": domain,
                        "chunk_index": idx,
                    },
                    "web_search_queries": web_search_queries,
                    "provider": response.get("provider", "vertex-ai"),
                }
            ],
        )
        results.append(result)

    # Resolve redirect URLs to canonical URLs
    resolved_results = await asyncio.gather(
        *[_resolve_redirect_result(r) for r in results]
    )

    return list(resolved_results)