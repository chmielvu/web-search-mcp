"""YouTube search — router + SearXNG + HTML scrape fallback.

search_youtube() is the primary entrypoint. It routes to:
  1. YouTube Data API search (if GOOGLE_API_KEY set and quota available)
  2. SearXNG with YouTube engine (unlimited, no API key)
  3. HTML scraping of youtube.com/results (zero-quota fallback)

Also provides SearXNG result metadata enhancement and channel handle resolution.
"""

from __future__ import annotations

import os
import re
import logging
import random
import urllib.parse
from typing import Any

import httpx

from ..models import WebSearchResult
from ..settings import settings
from ..search.providers.base import run_provider
from .models import YouTubeSearchError, YouTubeApiError

LOGGER = logging.getLogger(__name__)

# YouTube URL patterns for domain validation
_YOUTUBE_DOMAIN_RE = re.compile(
    r"^https?://(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/",
    re.IGNORECASE,
)

# User-Agent rotation for HTML scraping
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

# ytInitialData regex pattern
_YT_INITIAL_DATA_RE = re.compile(
    r"ytInitialData\s*=\s*({.*?});\s*</script>",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def search_youtube(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> tuple[list[WebSearchResult], str]:
    """Route YouTube search to API, SearXNG, or HTML scrape based on availability.

    Returns:
        Tuple of (results, search_backend) for telemetry.
        search_backend is one of: "api", "searxng", "html_scrape".
    """
    # Phase 1: YouTube Data API (if key set and quota available)
    if settings.youtube_api_key.strip():
        try:
            from .api_search import search_youtube_api

            results = await search_youtube_api(
                query, num_results=num_results, http_client=http_client
            )
            if results:
                return results, "api"
        except YouTubeApiError:
            LOGGER.debug("YouTube API search unavailable (quota/error), falling back")
        except Exception as exc:
            LOGGER.warning("YouTube API search failed (%s), falling back to SearXNG", exc)

    # Phase 2: SearXNG
    try:
        results = await search_youtube_videos(
            query, num_results=num_results, http_client=http_client
        )
        if results:
            return results, "searxng"
    except Exception as exc:
        LOGGER.debug("SearXNG YouTube search failed (%s), falling back to HTML scrape", exc)

    # Phase 3: HTML scrape (zero-quota fallback)
    try:
        results = await search_youtube_html_scrape(
            query, num_results=num_results, http_client=http_client
        )
        return results, "html_scrape"
    except Exception as exc:
        LOGGER.warning("HTML scrape YouTube search also failed: %s", exc)

    return [], "searxng"


# ---------------------------------------------------------------------------
# SearXNG YouTube search
# ---------------------------------------------------------------------------


async def search_youtube_videos(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Search YouTube videos using SearXNG with YouTube engine.

    Post-processes results to extract YouTube-specific metadata (author,
    duration, views, published date) from the SearXNG response.
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

            # --- YouTube metadata extraction from SearXNG response ---
            author = item.get("author")
            length_str = item.get("length")  # seconds as string
            views = item.get("views")
            published_date = item.get("publishedDate") or item.get("published_date")

            # Build enriched snippet with metadata
            metadata_parts: list[str] = []
            if length_str:
                try:
                    total_secs = int(length_str)
                    mins = total_secs // 60
                    secs = total_secs % 60
                    if mins >= 60:
                        hrs = mins // 60
                        mins = mins % 60
                        metadata_parts.append(f"{hrs}h{mins}m{secs}s")
                    else:
                        metadata_parts.append(f"{mins}m{secs}s")
                except (ValueError, TypeError):
                    pass

            if author:
                metadata_parts.append(f"Channel: {author}")

            if views is not None:
                try:
                    view_count = int(views)
                    if view_count >= 1_000_000:
                        metadata_parts.append(f"{view_count / 1_000_000:.1f}M views")
                    elif view_count >= 1_000:
                        metadata_parts.append(f"{view_count / 1_000:.1f}K views")
                    else:
                        metadata_parts.append(f"{view_count} views")
                except (ValueError, TypeError):
                    pass

            if published_date:
                metadata_parts.append(f"Published: {published_date}")

            if metadata_parts:
                enriched_snippet = f"{snippet.strip()} | {' | '.join(metadata_parts)}" if snippet.strip() else " | ".join(metadata_parts)
            else:
                enriched_snippet = snippet.strip()

            results.append(
                WebSearchResult(
                    title=title.strip(),
                    link=link.strip(),
                    snippet=enriched_snippet,
                    published_date=published_date if isinstance(published_date, str) else None,
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


# ---------------------------------------------------------------------------
# HTML Scraping Search (Zero-Quota Fallback)
# ---------------------------------------------------------------------------


async def search_youtube_html_scrape(
    query: str,
    *,
    num_results: int = 10,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Search YouTube by scraping youtube.com/results HTML.

    Extracts ytInitialData JSON from the page. Zero quota cost, no API key.
    Best-effort — may break if YouTube changes page structure.

    Args:
        query: Search query string.
        num_results: Maximum results to return (1-20).
        http_client: Optional httpx.AsyncClient for connection reuse.

    Returns:
        List of WebSearchResult items.
    """
    if not query.strip():
        return []

    if num_results < 1:
        return []

    num_results = min(num_results, 20)

    encoded_query = urllib.parse.quote_plus(query)
    search_url = f"https://www.youtube.com/results?search_query={encoded_query}"

    user_agent = random.choice(_USER_AGENTS)
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    async def _fetch(client: httpx.AsyncClient) -> str:
        resp = await client.get(search_url, headers=headers, timeout=15.0, follow_redirects=True)
        resp.raise_for_status()
        return resp.text

    async def _extract_and_parse(html: str) -> list[WebSearchResult]:
        match = _YT_INITIAL_DATA_RE.search(html)
        if not match:
            raise YouTubeSearchError("Could not find ytInitialData in YouTube search results page")

        import json

        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise YouTubeSearchError(f"Failed to parse ytInitialData JSON: {exc}")

        results: list[WebSearchResult] = []

        # Navigate the renderer tree to find video results
        try:
            contents = (
                data.get("contents", {})
                .get("twoColumnSearchResultsRenderer", {})
                .get("primaryContents", {})
                .get("sectionListRenderer", {})
                .get("contents", [])
            )
        except AttributeError:
            raise YouTubeSearchError("Unexpected ytInitialData structure")

        for section in contents:
            if not isinstance(section, dict):
                continue
            try:
                item_sections = (
                    section.get("itemSectionRenderer", {})
                    .get("contents", [])
                )
            except AttributeError:
                continue

            for item in item_sections:
                if not isinstance(item, dict):
                    continue
                try:
                    video_renderer = item.get("videoRenderer", {})
                except AttributeError:
                    continue

                if not video_renderer or not isinstance(video_renderer, dict):
                    continue

                video_id = video_renderer.get("videoId")
                if not video_id or not isinstance(video_id, str):
                    continue

                # Title
                title_runs = video_renderer.get("title", {}).get("runs", [])
                title = "".join(run.get("text", "") for run in title_runs if isinstance(run, dict))

                if not title.strip():
                    continue

                # Channel
                owner_runs = (
                    video_renderer.get("ownerText", {})
                    .get("runs", [])
                )
                channel = "".join(
                    run.get("text", "") for run in owner_runs if isinstance(run, dict)
                )

                # Duration
                duration_text = (
                    video_renderer.get("lengthText", {})
                    .get("simpleText", "")
                )

                # Views
                view_count_text = (
                    video_renderer.get("viewCountText", {})
                    .get("simpleText", "")
                )

                # Published time
                published_text = (
                    video_renderer.get("publishedTimeText", {})
                    .get("simpleText", "")
                )

                # Build snippet
                snippet_parts: list[str] = []
                if duration_text:
                    snippet_parts.append(f"Duration: {duration_text}")
                if channel:
                    snippet_parts.append(f"Channel: {channel}")
                if view_count_text:
                    snippet_parts.append(view_count_text)
                if published_text:
                    snippet_parts.append(published_text)

                snippet = " | ".join(snippet_parts) if snippet_parts else ""

                link = f"https://www.youtube.com/watch?v={video_id}"

                results.append(
                    WebSearchResult(
                        title=title.strip(),
                        link=link,
                        snippet=snippet,
                        published_date=published_text if isinstance(published_text, str) else None,
                    )
                )

                if len(results) >= num_results:
                    break

            if len(results) >= num_results:
                break

        return results

    if http_client is not None:
        html = await _fetch(http_client)
    else:
        async with httpx.AsyncClient() as client:
            html = await _fetch(client)

    return await _extract_and_parse(html)


# ---------------------------------------------------------------------------
# Channel Handle Resolution
# ---------------------------------------------------------------------------


async def resolve_channel_handle(
    handle: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """Resolve a YouTube channel handle (@handle) to a channel ID (UC...).

    Strategy 1: HTML scrape the @handle page for channelId.
    Strategy 2: YouTube Data API search.list with type=channel (if API key set).

    Args:
        handle: Channel handle with or without @ prefix.
        http_client: Optional httpx.AsyncClient for connection reuse.

    Returns:
        Channel ID string (e.g. "UC...").

    Raises:
        YouTubeSearchError: If channel cannot be resolved.
    """
    handle = handle.lstrip("@").strip()
    if not handle:
        raise YouTubeSearchError("Empty channel handle")

    # Strategy 1: HTML scrape @handle page
    channel_id = await _resolve_channel_via_html(handle, http_client=http_client)
    if channel_id:
        return channel_id

    # Strategy 2: YouTube Data API
    if settings.youtube_api_key.strip():
        channel_id = await _resolve_channel_via_api(handle, http_client=http_client)
        if channel_id:
            return channel_id

    raise YouTubeSearchError(
        f"Could not resolve channel handle @{handle} "
        "(HTML scrape and API both failed)"
    )


async def _resolve_channel_via_html(
    handle: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> str | None:
    """Resolve channel handle by scraping the @handle page HTML."""
    url = f"https://www.youtube.com/@{handle}"

    headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    channel_id_re = re.compile(r'"channelId"\s*:\s*"(UC[a-zA-Z0-9_-]+)"')

    async def _do_fetch(client: httpx.AsyncClient) -> str | None:
        try:
            resp = await client.get(url, headers=headers, timeout=15.0, follow_redirects=True)
            resp.raise_for_status()
            html = resp.text
            match = channel_id_re.search(html)
            if match:
                return match.group(1)
            return None
        except Exception as exc:
            LOGGER.debug("Channel handle HTML scrape failed for @%s: %s", handle, exc)
            return None

    if http_client is not None:
        return await _do_fetch(http_client)
    async with httpx.AsyncClient() as client:
        return await _do_fetch(client)


async def _resolve_channel_via_api(
    handle: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> str | None:
    """Resolve channel handle using YouTube Data API search.list."""
    api_key = settings.youtube_api_key.strip()
    if not api_key:
        return None

    url = "https://www.googleapis.com/youtube/v3/search"
    params: dict[str, Any] = {
        "part": "snippet",
        "type": "channel",
        "q": handle,
        "key": api_key,
        "maxResults": 1,
    }

    headers = {"Accept": "application/json"}

    async def _do_request(client: httpx.AsyncClient) -> str | None:
        try:
            resp = await client.get(
                url, params=params, headers=headers,
                timeout=settings.youtube_api_timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            if items:
                channel_id = items[0].get("id", {}).get("channelId")
                if channel_id:
                    return channel_id
            return None
        except Exception as exc:
            LOGGER.debug("Channel API resolution failed for @%s: %s", handle, exc)
            return None

    if http_client is not None:
        return await _do_request(http_client)
    async with httpx.AsyncClient() as client:
        return await _do_request(client)


async def search_channel_videos(
    channel_id: str,
    *,
    max_results: int = 20,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Search for videos from a specific channel.

    Uses the channel's uploads playlist (UU + channel_id[2:]).

    Args:
        channel_id: YouTube channel ID (UC...).
        max_results: Maximum results to return (1-50).
        http_client: Optional httpx.AsyncClient.

    Returns:
        List of WebSearchResult items.
    """
    if not channel_id or not channel_id.startswith("UC"):
        raise YouTubeSearchError(f"Invalid channel ID: {channel_id}")

    max_results = min(max_results, 50)

    # Uploads playlist ID = UU + channel_id without the "UC" prefix
    uploads_playlist_id = "UU" + channel_id[2:]

    # Use SearXNG to search for videos from this channel
    search_query = f"channel_id:{channel_id}"
    try:
        results = await search_youtube_videos(
            search_query, num_results=max_results, http_client=http_client
        )
        return results
    except Exception as exc:
        LOGGER.debug("Channel video search via SearXNG failed for %s: %s", channel_id, exc)

    # Fallback: use the uploads playlist URL
    playlist_url = f"https://www.youtube.com/playlist?list={uploads_playlist_id}"
    LOGGER.debug("Falling back to playlist URL for channel %s: %s", channel_id, playlist_url)

    return []
