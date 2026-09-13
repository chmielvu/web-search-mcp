"""YouTube discovery cascade for quick web search (API → SearXNG → HTML).

Ported from ``prototypes/quick_web_search_v2.py`` and wired to
:mod:`~kindly_web_search_mcp_server.settings` instead of raw environment
reads. This module replaces the retired ``youtube/search.py`` cascade; the
transcript stack (``youtube/transcript.py``, ``cascade.py``, ``channel_api.py``)
is untouched.
"""

from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from ...settings import settings

LOGGER = logging.getLogger(__name__)

_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
_YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
_YOUTUBE_RESULTS_URL = "https://www.youtube.com/results"

_DEFAULT_TIMEOUT_SECONDS = 45.0
_DEFAULT_MAX_RESULTS = 5


class YouTubeVideo(BaseModel):
    """Provider-neutral YouTube video metadata."""

    provider: str = "youtube_html"
    title: str
    url: str
    snippet: str = ""
    video_id: str | None = None
    channel_id: str | None = None
    channel_title: str | None = None
    published_at: str | None = None
    duration: str | None = None
    views: str | None = None


class YouTubeWarning(BaseModel):
    """A provider failure retained beside a partial successful response."""

    provider: str
    kind: str
    message: str
    hint: str
    retryable: bool = False
    context: dict[str, Any] = Field(default_factory=dict)


class YouTubeUsage(BaseModel):
    """Small common record of external requests made by the cascade."""

    provider: str
    requests: int
    endpoint: str
    details: dict[str, Any] = Field(default_factory=dict)


class YouTubeOutcome(BaseModel):
    """Normalized result of the YouTube provider cascade."""

    videos: list[YouTubeVideo] = Field(default_factory=list)
    providers_attempted: list[str] = Field(default_factory=list)
    providers: list[str] = Field(default_factory=list)
    truncated: bool = False
    warnings: list[YouTubeWarning] = Field(default_factory=list)
    usage: list[YouTubeUsage] = Field(default_factory=list)


def _text(value: Any) -> str:
    """Return stripped string text, or an empty string for other values."""
    return value.strip() if isinstance(value, str) else ""


def _text_or_none(value: Any) -> str | None:
    """Return stripped string text, or None when no text is available."""
    return _text(value) or None


def _warn(provider: str, error: BaseException) -> YouTubeWarning:
    """Convert a provider exception into warning data."""
    message = _text(str(error))[:300]
    return YouTubeWarning(
        provider=provider,
        kind=type(error).__name__,
        message=message or type(error).__name__,
        hint="Inspect provider connectivity and retry.",
        retryable=True,
    )


def _is_youtube_url(value: str) -> bool:
    """Return whether a URL belongs to YouTube."""
    parsed = urlparse(value)
    return (
        parsed.scheme in {"http", "https"} and (parsed.hostname or "").casefold() in _YOUTUBE_HOSTS
    )


def _renderer_text(value: Any) -> str:
    """Read text from a YouTube renderer field."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        runs = value.get("runs")
        if isinstance(runs, list):
            return "".join(_text(item.get("text")) for item in runs if isinstance(item, dict))
        return _text(value.get("simpleText"))
    return ""


def _renderer_clean(renderer: dict[str, Any], key: str) -> str:
    """Return decoded, stripped text from one YouTube renderer field."""
    return html.unescape(_renderer_text(renderer.get(key))).strip()


def _iter_youtube_renderers(value: Any) -> Any:
    """Yield video renderers in document order."""
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            renderer = node.get("videoRenderer")
            if isinstance(renderer, dict):
                yield renderer
            stack.extend(reversed(node.values()))
        elif isinstance(node, list):
            stack.extend(reversed(node))


def _extract_youtube_data(page: str) -> dict[str, Any]:
    """Decode the embedded ``ytInitialData`` object."""
    match = re.search(r"ytInitialData\s*=\s*", page)
    start = page.find("{", match.end()) if match else -1
    if start < 0:
        raise RuntimeError("YouTube HTML did not contain ytInitialData.")
    try:
        data, _ = json.JSONDecoder().raw_decode(page, start)
    except json.JSONDecodeError as exc:
        raise RuntimeError("YouTube initial-data JSON could not be decoded.") from exc
    if not isinstance(data, dict):
        raise RuntimeError("YouTube initial-data JSON was not an object.")
    return data


async def _youtube_api(
    query: str,
    max_results: int,
    timeout_seconds: float,
    client: httpx.AsyncClient,
) -> tuple[list[YouTubeVideo], YouTubeUsage]:
    """Use the YouTube Data API as the strongest metadata source."""
    api_key = settings.youtube_api_key.strip()
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY is not configured; allow the SearXNG/HTML fallbacks.")
    params: dict[str, Any] = {
        "key": api_key,
        "q": query,
        "type": "video",
        "part": "snippet",
        "maxResults": min(max_results, 50),
        "safeSearch": "moderate",
    }
    if settings.youtube_api_language.strip():
        params["relevanceLanguage"] = settings.youtube_api_language.strip()
    if settings.youtube_api_region.strip():
        params["regionCode"] = settings.youtube_api_region.strip()
    response = await client.get(
        _YOUTUBE_SEARCH_URL,
        params=params,
        headers={"Accept": "application/json"},
        timeout=timeout_seconds,
    )
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("YouTube Data API returned invalid JSON.") from exc
    if response.status_code >= 400:
        reason = ""
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict):
                errors = error.get("errors")
                if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                    reason = _text(errors[0].get("reason"))
        raise RuntimeError(
            f"YouTube Data API returned HTTP {response.status_code}"
            f"{f' ({reason})' if reason else ''}. "
            "Check GOOGLE_API_KEY, API enablement, and quota; a fallback may still work."
        )
    videos: list[YouTubeVideo] = []
    for item in data.get("items", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        video_id = raw_id.get("videoId") if isinstance(raw_id, dict) else None
        raw_snippet = item.get("snippet")
        snippet = raw_snippet if isinstance(raw_snippet, dict) else {}
        video_id = _text(video_id)
        title = _text(snippet.get("title"))
        if not video_id or not title:
            continue
        videos.append(
            YouTubeVideo(
                provider="youtube_api",
                title=title,
                url=f"https://www.youtube.com/watch?v={video_id}",
                snippet=_text(snippet.get("description")),
                video_id=video_id,
                channel_id=_text_or_none(snippet.get("channelId")),
                channel_title=_text_or_none(snippet.get("channelTitle")),
                published_at=_text_or_none(snippet.get("publishedAt")),
            )
        )
    return videos, YouTubeUsage(
        provider="youtube_api",
        requests=1,
        endpoint=_YOUTUBE_SEARCH_URL,
        details={"method": "search.list", "quota_units": 1},
    )


async def _youtube_searxng(
    query: str,
    max_results: int,
    timeout_seconds: float,
    client: httpx.AsyncClient,
) -> tuple[list[YouTubeVideo], YouTubeUsage]:
    """Use configured SearXNG as the middle fallback."""
    base_url = settings.searxng_base_url.strip().rstrip("/")
    if not base_url:
        raise RuntimeError("SEARXNG_BASE_URL is not configured.")
    engine = settings.youtube_search_engine.strip() or "youtube"
    response = await client.get(
        f"{base_url}/search",
        params={
            "q": query,
            "format": "json",
            "engines": engine,
        },
        headers={"Accept": "application/json"},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("SearXNG returned invalid JSON for YouTube.") from exc
    items = data.get("results", []) if isinstance(data, dict) else []
    if not isinstance(items, list):
        raise RuntimeError("SearXNG returned no result list for YouTube.")
    videos: list[YouTubeVideo] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = _text(item.get("url"))
        title = _text(item.get("title"))
        if not url or not title or not _is_youtube_url(url):
            continue
        videos.append(
            YouTubeVideo(
                provider="searxng_youtube",
                title=title,
                url=url,
                snippet=_text(item.get("content")),
            )
        )
        if len(videos) >= max_results:
            break
    return videos, YouTubeUsage(
        provider="searxng_youtube",
        requests=1,
        endpoint=f"{base_url}/search",
        details={"engine": engine},
    )


async def _youtube_html(
    query: str,
    max_results: int,
    timeout_seconds: float,
    client: httpx.AsyncClient,
) -> tuple[list[YouTubeVideo], YouTubeUsage]:
    """Use YouTube search HTML as the final fallback."""
    response = await client.get(
        _YOUTUBE_RESULTS_URL,
        params={"search_query": query},
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.8",
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    videos: list[YouTubeVideo] = []
    for renderer in _iter_youtube_renderers(_extract_youtube_data(response.text)):
        video_id = _text(renderer.get("videoId"))
        title = _renderer_clean(renderer, "title")
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) or not title:
            continue
        channel = _renderer_clean(renderer, "ownerText")
        duration = _renderer_clean(renderer, "lengthText")
        views = _renderer_clean(renderer, "viewCountText")
        published = _renderer_clean(renderer, "publishedTimeText")
        videos.append(
            YouTubeVideo(
                provider="youtube_html",
                title=title,
                url=f"https://www.youtube.com/watch?v={video_id}",
                snippet=" | ".join(part for part in (duration, channel, views, published) if part),
                video_id=video_id,
                channel_title=channel or None,
                published_at=published or None,
                duration=duration or None,
                views=views or None,
            )
        )
        if len(videos) >= max_results:
            break
    return videos, YouTubeUsage(
        provider="youtube_html",
        requests=1,
        endpoint=_YOUTUBE_RESULTS_URL,
        details={"quota_units": 0},
    )


_ProviderFn = Callable[
    [str, int, float, httpx.AsyncClient],
    Awaitable[tuple[list[YouTubeVideo], YouTubeUsage]],
]


async def fetch_youtube_outcome(
    query: str,
    *,
    max_results: int = _DEFAULT_MAX_RESULTS,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> YouTubeOutcome:
    """Run the YouTube API → SearXNG → HTML cascade until videos arrive.

    Args:
        query: Video search query text.
        max_results: Upper bound on videos to return.
        timeout_seconds: Per-provider HTTP timeout.

    Returns:
        YouTubeOutcome with videos plus per-provider warnings/usage.

    Raises:
        ValueError: If the query is blank.
        RuntimeError: If every provider fails or returns no usable videos.
    """
    if not query or not query.strip():
        raise ValueError("mode='youtube' requires a non-blank query.")
    providers: tuple[tuple[str, _ProviderFn], ...] = (
        ("youtube_api", _youtube_api),
        ("searxng_youtube", _youtube_searxng),
        ("youtube_html", _youtube_html),
    )
    warnings: list[YouTubeWarning] = []
    usage: list[YouTubeUsage] = []
    videos: list[YouTubeVideo] = []
    attempted: list[str] = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for name, provider in providers:
            attempted.append(name)
            try:
                videos, item = await provider(query.strip(), max_results, timeout_seconds, client)
            except Exception as exc:
                warnings.append(_warn(name, exc))
                continue
            usage.append(item)
            if videos:
                break
    if not videos:
        raise RuntimeError(
            "All YouTube providers returned no usable videos. "
            "Configure GOOGLE_API_KEY or SEARXNG_BASE_URL and retry."
        )
    return YouTubeOutcome(
        videos=videos[:max_results],
        providers_attempted=attempted,
        providers=list(dict.fromkeys(video.provider for video in videos)),
        warnings=warnings,
        usage=usage,
    )
