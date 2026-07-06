"""Batch video metadata enrichment via YouTube Data API v3 videos.list endpoint.

Cost: 1 quota unit per call (up to 50 IDs per request).
Best-effort: failures return an empty dict so callers can still return
un-enriched search results.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from ..models import WebSearchResult
from ..settings import settings
from .api_quota import get_youtube_api_quota_tracker

logger = logging.getLogger(__name__)

_ENRICHMENT_URL = "https://www.googleapis.com/youtube/v3/videos"
_ENRICHMENT_COST = 1  # units per videos.list call
_MAX_IDS = 50

_ISO_DURATION_RE = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------


def _parse_iso8601_duration(duration: str) -> float:
    """Parse ISO 8601 duration string to seconds.

    Examples:
        PT4M13S  -> 253.0
        PT1H2M3S -> 3723.0
        PT0S     -> 0.0
    """
    match = _ISO_DURATION_RE.match(duration)
    if not match:
        return 0.0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return float(hours * 3600 + minutes * 60 + seconds)


def _format_duration(seconds: float) -> str:
    """Format seconds as compact 'Xh Ym Zs' string."""
    total = int(seconds)
    if total <= 0:
        return "0s"
    parts: list[str] = []
    h, remainder = divmod(total, 3600)
    if h:
        parts.append(f"{h}h")
    m, s = divmod(remainder, 60)
    if m:
        parts.append(f"{m}m")
    if s or not parts:
        parts.append(f"{s}s")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_video_id_from_link(link: str) -> str | None:
    """Extract video ID from a YouTube watch URL."""
    try:
        parsed = urlparse(link)
        params = parse_qs(parsed.query)
        vid = params.get("v", [None])[0]
        return vid if isinstance(vid, str) and vid.strip() else None
    except Exception:
        return None


def _safe_int(value: Any) -> int | None:
    """Safely convert a value to int, returning None on failure."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _format_enrichment_line(meta: dict[str, Any]) -> str:
    """Build a compact pipe-separated metadata string."""
    parts: list[str] = []
    duration = meta.get("duration_seconds")
    if duration and duration > 0:
        parts.append(f"Duration: {_format_duration(duration)}")
    views = meta.get("view_count")
    if views is not None and views >= 0:
        parts.append(f"{views:,} views")
    likes = meta.get("like_count")
    if likes is not None and likes > 0:
        parts.append(f"{likes:,} likes")
    published = meta.get("published_date")
    if published:
        parts.append(f"Published: {published}")
    if meta.get("has_captions"):
        parts.append("Captions available")
    return " | ".join(parts)


def _parse_enrichment_response(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Parse videos.list JSON response into a metadata dict."""
    items = data.get("items", [])
    if not isinstance(items, list):
        return {}

    result: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue

        video_id = item.get("id", "")
        if not isinstance(video_id, str) or not video_id:
            continue

        snippet = item.get("snippet", {})
        content = item.get("contentDetails", {})
        stats = item.get("statistics", {})

        thumbnails = snippet.get("thumbnails", {})
        thumbnail_url = (
            thumbnails.get("high", {}).get("url")
            or thumbnails.get("medium", {}).get("url")
            or thumbnails.get("default", {}).get("url")
            or ""
        )

        published_raw = snippet.get("publishedAt", "")
        published_date = published_raw[:10] if isinstance(published_raw, str) else ""

        result[video_id] = {
            "duration_seconds": _parse_iso8601_duration(content.get("duration", "PT0S")),
            "view_count": _safe_int(stats.get("viewCount")),
            "like_count": _safe_int(stats.get("likeCount")),
            "channel_title": snippet.get("channelTitle", ""),
            "thumbnail_url": thumbnail_url,
            "has_captions": content.get("caption", "false") == "true",
            "published_date": published_date,
        }

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def enrich_video_metadata(
    video_ids: list[str],
    *,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, dict[str, Any]]:
    """Batch fetch metadata for up to 50 video IDs via videos.list.

    Cost: 1 quota unit per call.

    Returns:
        Dict mapping video_id to metadata dict.  Empty dict on failure
        or when quota is exhausted.
    """
    if not video_ids:
        return {}

    ids = video_ids[:_MAX_IDS]

    api_key = settings.youtube_api_key.strip()
    if not api_key:
        return {}

    tracker = get_youtube_api_quota_tracker()
    if not tracker.can_afford(_ENRICHMENT_COST):
        logger.warning(
            "Skipping video enrichment: quota exhausted (%d/%d units)",
            tracker.snapshot()["used"],
            tracker.snapshot()["daily_quota"],
        )
        return {}

    timeout = settings.youtube_api_timeout_seconds

    params: dict[str, Any] = {
        "key": api_key,
        "id": ",".join(ids),
        "part": "snippet,contentDetails,statistics",
    }

    async def _fetch(client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
        resp = await client.get(
            _ENRICHMENT_URL,
            params=params,
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        if resp.status_code == 403:
            tracker.record_call(success=False, units=_ENRICHMENT_COST)
            logger.warning("Video enrichment returned 403 (quota/key issue)")
            return {}
        resp.raise_for_status()
        data = resp.json()
        tracker.record_call(success=True, units=_ENRICHMENT_COST)
        return _parse_enrichment_response(data)

    try:
        if http_client is not None:
            return await _fetch(http_client)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await _fetch(client)
    except Exception as exc:
        logger.debug("Video enrichment failed: %s", exc)
        return {}


def merge_enrichment_into_results(
    search_results: list[WebSearchResult],
    metadata: dict[str, dict[str, Any]],
) -> list[WebSearchResult]:
    """Enrich WebSearchResult snippets with video metadata.

    Appends duration, views, likes, published date, and caption
    availability to the snippet field.  The original snippet text is
    preserved as the first part.
    """
    if not metadata:
        return search_results

    enriched: list[WebSearchResult] = []
    for result in search_results:
        video_id = _extract_video_id_from_link(result.link)
        if video_id and video_id in metadata:
            meta = metadata[video_id]
            enrichment_line = _format_enrichment_line(meta)

            updates: dict[str, Any] = {}
            if enrichment_line:
                new_snippet = (
                    f"{result.snippet}\n{enrichment_line}" if result.snippet else enrichment_line
                )
                updates["snippet"] = new_snippet

            if meta.get("published_date") and not result.published_date:
                updates["published_date"] = meta["published_date"]

            if updates:
                result = result.model_copy(update=updates)
        enriched.append(result)
    return enriched
