from __future__ import annotations

import os
import logging
import re
from typing import Any

import httpx

from ..models import WebSearchResult
from ..settings import settings


LOGGER = logging.getLogger(__name__)

# YouTube URL patterns for domain validation
_YOUTUBE_DOMAIN_RE = re.compile(
    r"^https?://(?:www\.)?(?:youtube\.com|youtu\.be)/",
    re.IGNORECASE,
)


class YouTubeSearchError(RuntimeError):
    """Custom error for YouTube search failures."""
    pass


async def search_youtube_videos(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """
    Search YouTube videos using SearXNG with YouTube engine.

    Uses SearXNG's built-in YouTube engine to search for videos.
    This leverages the existing SearXNG infrastructure without adding
    new dependencies.

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

    # Cap results at 20
    num_results = min(num_results, 20)

    base_url = os.environ.get("SEARXNG_BASE_URL", "").strip()
    if not base_url:
        raise YouTubeSearchError(
            "SEARXNG_BASE_URL is not configured. "
            "YouTube search requires SearXNG instance."
        )

    base_url = base_url.rstrip("/")
    url = f"{base_url}/search"

    # Use YouTube engine (settings.youtube_search_engine defaults to "youtube")
    engine = settings.youtube_search_engine

    params: dict[str, Any] = {
        "q": query,
        "format": "json",
        "engines": engine,
    }

    # Optional parameters from environment
    language = (os.environ.get("SEARXNG_LANGUAGE") or "").strip()
    if language:
        params["language"] = language

    safesearch = (os.environ.get("SEARXNG_SAFESEARCH") or "").strip()
    if safesearch:
        params["safesearch"] = safesearch

    headers = {
        "User-Agent": os.environ.get(
            "SEARXNG_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
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

    try:
        if http_client is None:
            async with httpx.AsyncClient(timeout=30) as client:
                data = await _do_request(client)
        else:
            data = await _do_request(http_client)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 403:
            raise YouTubeSearchError(
                "SearXNG returned 403 Forbidden. JSON output may be disabled."
            ) from exc
        if status == 429:
            raise YouTubeSearchError("SearXNG rate limited (429 Too Many Requests).") from exc
        raise YouTubeSearchError(f"SearXNG returned HTTP {status}.") from exc
    except httpx.TimeoutException:
        raise YouTubeSearchError(f"SearXNG request timed out after {timeout_seconds}s.")
    except Exception as exc:
        raise YouTubeSearchError(f"SearXNG request failed: {type(exc).__name__}: {exc}") from exc

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

        # Validate required fields
        if not isinstance(title, str) or not title.strip():
            continue
        if not isinstance(link, str) or not link.strip():
            continue

        # Domain validation: only accept youtube.com / youtu.be URLs
        if not _YOUTUBE_DOMAIN_RE.match(link.strip()):
            LOGGER.debug(
                "Skipping non-YouTube URL in YouTube search results: %s", link
            )
            continue

        if not isinstance(snippet, str):
            snippet = ""  # Allow empty snippets for video results

        # Mark as YouTube resource type
        result = WebSearchResult(
            title=title.strip(),
            link=link.strip(),
            snippet=snippet.strip() if snippet else "",
            resource_type="youtube",
            providers=["searxng_youtube"],
        )
        results.append(result)

        if len(results) >= num_results:
            break

    # Log warning if all results were filtered out
    if raw_results and not results:
        LOGGER.warning(
            "All %d SearXNG YouTube results were non-YouTube URLs — "
            "SearXNG YouTube engine may be misconfigured",
            len(raw_results),
        )

    return results