"""YouTube Data API v3 access: video discovery and channel/uploads enumeration.

Discovery cascade (API → SearXNG → HTML) plus channel enumeration through
``channels.list``/``playlistItems.list`` with the shared in-memory quota
tracker. Transcript acquisition lives in ``content/youtube_transcripts.py``.
"""

from __future__ import annotations

import html
import json
import logging
import re
import threading
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from ...models import YouTubeChannelVideo
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


# ---------------------------------------------------------------------------
# YouTube Data API v3 channel/uploads enumeration + quota (moved from youtube/)
# ---------------------------------------------------------------------------

_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
_PLAYLIST_ITEMS_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
_CHANNEL_API_CALL_COST = 1

# Quota warning thresholds
_QUOTA_WARN_THRESHOLD = 0.80  # 80%
_QUOTA_HALT_THRESHOLD = 1.00  # 100%


class YouTubeApiError(RuntimeError):
    """Custom error for YouTube Data API v3 failures."""


class YouTubeApiQuotaTracker:
    """In-memory daily quota tracker for YouTube Data API v3.

    Thread-safe. Resets when the UTC date changes.
    Default daily quota: 10,000 units (Google's default).
    """

    def __init__(self, daily_quota: int = 10_000) -> None:
        self._daily_quota = daily_quota
        self._lock = threading.Lock()
        self._today: str = ""
        self._used: int = 0
        self._call_count: int = 0
        self._failures: int = 0

    def _maybe_rollover(self) -> None:
        """Reset counters if the UTC date has changed."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if today != self._today:
            if self._today:
                LOGGER.info(
                    "YouTube API quota rollover: %s → %s (used %d/%d units)",
                    self._today,
                    today,
                    self._used,
                    self._daily_quota,
                )
            self._today = today
            self._used = 0
            self._call_count = 0
            self._failures = 0

    def can_afford(self, units: int) -> bool:
        """Check whether the requested units fit within the daily quota."""
        with self._lock:
            self._maybe_rollover()
            return (self._used + units) <= self._daily_quota

    def record_call(self, success: bool, units: int) -> None:
        """Record a quota-consuming API call."""
        with self._lock:
            self._maybe_rollover()
            self._used += units
            self._call_count += 1
            if not success:
                self._failures += 1

            usage_ratio = self._used / self._daily_quota if self._daily_quota else 0

            if usage_ratio >= _QUOTA_HALT_THRESHOLD:
                LOGGER.warning(
                    "YouTube API daily quota EXHAUSTED: %d/%d units used (%d calls, %d failures)",
                    self._used,
                    self._daily_quota,
                    self._call_count,
                    self._failures,
                )
            elif usage_ratio >= _QUOTA_WARN_THRESHOLD:
                LOGGER.warning(
                    "YouTube API daily quota at %.0f%%: %d/%d units used",
                    usage_ratio * 100,
                    self._used,
                    self._daily_quota,
                )

    def snapshot(self) -> dict[str, Any]:
        """Return current quota state for diagnostics."""
        with self._lock:
            self._maybe_rollover()
            return {
                "date": self._today,
                "daily_quota": self._daily_quota,
                "used": self._used,
                "remaining": max(0, self._daily_quota - self._used),
                "usage_pct": round(self._used / self._daily_quota * 100, 1)
                if self._daily_quota
                else 0,
                "call_count": self._call_count,
                "failures": self._failures,
            }


# Module-level singleton
_quota_tracker: YouTubeApiQuotaTracker | None = None
_tracker_lock = threading.Lock()


def get_youtube_api_quota_tracker() -> YouTubeApiQuotaTracker:
    """Return the singleton quota tracker."""
    global _quota_tracker
    if _quota_tracker is None:
        with _tracker_lock:
            if _quota_tracker is None:
                _quota_tracker = YouTubeApiQuotaTracker(
                    daily_quota=settings.youtube_api_daily_quota,
                )
    return _quota_tracker


def _channel_selector(value: str) -> tuple[str, str]:
    """Return Data API parameter name and value for a channel identifier."""
    raw = value.strip()
    if not raw:
        raise YouTubeApiError("Channel identifier cannot be empty")
    if raw.startswith("UC") and len(raw) >= 20:
        return "id", raw
    if "/@" in raw:
        raw = raw.split("/@", 1)[1].split("/", 1)[0]
    elif raw.startswith("http"):
        path = urlparse(raw).path.strip("/")
        raw = path.split("/@", 1)[-1] if "/@" in path else path.split("/")[-1]
    return "forHandle", raw.lstrip("@")


async def list_channel_videos(
    channel: str,
    *,
    max_results: int = 100,
    page_token: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> tuple[str, list[YouTubeChannelVideo], str | None]:
    """Enumerate a channel through its uploads playlist.

    ``channels.list`` resolves the uploads playlist and ``playlistItems.list``
    pages it; both calls cost 1 quota unit each and share the tracker above.
    """
    if max_results < 1:
        return "", [], page_token
    max_results = min(max_results, 5000)
    api_key = settings.youtube_api_key.strip()
    if not api_key:
        raise YouTubeApiError("GOOGLE_API_KEY is required for channel enumeration")

    selector, selector_value = _channel_selector(channel)
    tracker = get_youtube_api_quota_tracker()
    timeout = settings.youtube_api_timeout_seconds

    async def _request(
        client: httpx.AsyncClient, url: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        if not tracker.can_afford(_CHANNEL_API_CALL_COST):
            raise YouTubeApiError("YouTube API quota exhausted before channel enumeration")
        try:
            response = await client.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise YouTubeApiError("YouTube API returned a non-object response")
            tracker.record_call(success=True, units=_CHANNEL_API_CALL_COST)
            return payload
        except Exception:
            tracker.record_call(success=False, units=_CHANNEL_API_CALL_COST)
            raise

    async def _run(
        client: httpx.AsyncClient,
    ) -> tuple[str, list[YouTubeChannelVideo], str | None]:
        channel_payload = await _request(
            client,
            _CHANNELS_URL,
            {"part": "snippet,contentDetails", selector: selector_value, "key": api_key},
        )
        items = channel_payload.get("items") or []
        if not items or not isinstance(items[0], dict):
            raise YouTubeApiError(f"Channel not found: {channel}")
        channel_item = items[0]
        channel_id = str(channel_item.get("id") or "")
        snippet = channel_item.get("snippet") or {}
        details = channel_item.get("contentDetails") or {}
        related = details.get("relatedPlaylists") or {}
        uploads_playlist = related.get("uploads")
        if not channel_id or not uploads_playlist:
            raise YouTubeApiError(f"Channel has no uploads playlist: {channel_id or channel}")

        videos: list[YouTubeChannelVideo] = []
        current_token = page_token
        while len(videos) < max_results:
            page_size = min(50, max_results - len(videos))
            params: dict[str, Any] = {
                "part": "snippet,contentDetails",
                "playlistId": uploads_playlist,
                "maxResults": page_size,
                "key": api_key,
            }
            if current_token:
                params["pageToken"] = current_token
            page = await _request(client, _PLAYLIST_ITEMS_URL, params)
            for item in page.get("items") or []:
                if not isinstance(item, dict):
                    continue
                item_snippet = item.get("snippet") or {}
                content = item.get("contentDetails") or {}
                resource = item_snippet.get("resourceId") or content.get("resourceId") or {}
                video_id = resource.get("videoId") if isinstance(resource, dict) else None
                if not isinstance(video_id, str) or not video_id:
                    continue
                videos.append(
                    YouTubeChannelVideo(
                        video_id=video_id,
                        video_url=f"https://www.youtube.com/watch?v={video_id}",
                        title=str(item_snippet.get("title") or ""),
                        description=str(item_snippet.get("description") or ""),
                        channel_id=channel_id,
                        channel_title=str(
                            item_snippet.get("channelTitle") or snippet.get("title") or ""
                        )
                        or None,
                        published_at=item_snippet.get("publishedAt"),
                        position=item_snippet.get("position"),
                    )
                )
                if len(videos) >= max_results:
                    break
            next_token = page.get("nextPageToken")
            if len(videos) >= max_results:
                return channel_id, videos, str(next_token) if next_token else None
            if not next_token:
                return channel_id, videos, None
            current_token = str(next_token)
        return channel_id, videos, current_token

    if http_client is not None:
        return await _run(http_client)
    async with httpx.AsyncClient(headers={"Accept": "application/json"}) as client:
        return await _run(client)
