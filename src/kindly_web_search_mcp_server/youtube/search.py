"""YouTube search — router + SearXNG fallback.

search_youtube() is the primary entrypoint. It routes to the YouTube Data API
search provider when GOOGLE_API_KEY is set, falling back to SearXNG otherwise.
"""

from __future__ import annotations

import os
import re
import logging
from typing import Any

import httpx

from ..models import WebSearchResult
from ..settings import settings
from ..search.providers.base import run_provider
from .models import YouTubeSearchError

LOGGER = logging.getLogger(__name__)

# YouTube URL patterns for domain validation
_YOUTUBE_DOMAIN_RE = re.compile(
    r"^https?://(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/",
    re.IGNORECASE,
)


async def search_youtube(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> tuple[list[WebSearchResult], str]:
    """Route YouTube search to API or SearXNG based on available credentials.

    Returns:
        Tuple of (results, search_backend) for telemetry.
    """
    if settings.youtube_api_key.strip():
        try:
            from .api_search import search_youtube_api

            results = await search_youtube_api(
                query, num_results=num_results, http_client=http_client
            )
            return results, "api"
        except Exception as exc:
            LOGGER.warning("YouTube API search failed (%s), falling back to SearXNG", exc)

    results = await search_youtube_videos(query, num_results=num_results, http_client=http_client)
    return results, "searxng"


async def search_youtube_videos(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Search YouTube videos using SearXNG with YouTube engine.

    Args:
        query: Search query string.
        num_results: Maximum number of results to return (1-20).
        http_client: Optional httpx.AsyncClient for connection reuse.

    Returns:
        List of WebSearchResult objects with video metadata.

    Raises:
        YouTubeSearchError: If SearXNG is unavailable or search fails.
    """
    if not query.strip():
        return []

    if num_results < 1:
        return []

    num_results = min(num_results, 20)

    base_url = os.environ.get("SEARXNG_BASE_URL", "").strip()
    if not base_url:
        raise YouTubeSearchError(
            "SEARXNG_BASE_URL is not configured. YouTube search requires SearXNG instance."
        )

    base_url = base_url.rstrip("/")
    url = f"{base_url}/search"

    engine = settings.youtube_search_engine

    params: dict[str, Any] = {
        "q": query,
        "format": "json",
        "engines": engine,
    }

    language = (os.environ.get("SEARXNG_LANGUAGE") or "").strip()
    if language:
        params["language"] = language

    safesearch = (os.environ.get("SEARXNG_SAFESEARCH") or "").strip()
    if safesearch:
        params["safesearch"] = safesearch

    headers = {
        "User-Agent": os.environ.get(
            "SEARXNG_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        ).strip(),
        "Accept": "application/json",
    }

    timeout_seconds = 30.0
    raw_timeout = (os.environ.get("SEARXNG_TIMEOUT_SECONDS") or "").strip()
    if raw_timeout:
        try:
            timeout_seconds = float(raw_timeout)
        except ValueError:
            pass

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        resp = await client.get(url, params=params, headers=headers, timeout=timeout_seconds)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise YouTubeSearchError("SearXNG response was not a JSON object")
        return data

    def _parse_response(data: dict[str, Any]) -> list[WebSearchResult]:
        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            raise YouTubeSearchError("SearXNG response missing `results` list.")

        if not raw_results:
            LOGGER.debug("SearXNG YouTube search returned empty results for query=%r", query)

        results: list[WebSearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue

            title = item.get("title")
            link = item.get("url")
            snippet = item.get("content")

            if not isinstance(title, str) or not title.strip():
                continue
            if not isinstance(link, str) or not link.strip():
                continue

            if not _YOUTUBE_DOMAIN_RE.match(link.strip()):
                LOGGER.debug("Skipping non-YouTube URL in YouTube search results: %s", link)
                continue

            if not isinstance(snippet, str):
                snippet = ""

            results.append(
                WebSearchResult(
                    title=title.strip(),
                    link=link.strip(),
                    snippet=snippet.strip() if snippet else "",
                )
            )

            if len(results) >= num_results:
                break

        if raw_results and not results:
            LOGGER.warning(
                "All %d SearXNG YouTube results were non-YouTube URLs — "
                "SearXNG YouTube engine may be misconfigured",
                len(raw_results),
            )

        return results

    return await run_provider(
        "searxng_youtube",
        query,
        num_results,
        request=_do_request,
        parse_response=_parse_response,
        http_client=http_client,
        timeout_seconds=timeout_seconds,
    )
