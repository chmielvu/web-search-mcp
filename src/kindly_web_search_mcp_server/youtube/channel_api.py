"""YouTube Data API channel and uploads-playlist enumeration."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from ..models import YouTubeChannelVideo
from ..settings import settings
from .api_quota import get_youtube_api_quota_tracker
from .models import YouTubeApiError

_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
_PLAYLIST_ITEMS_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
_API_CALL_COST = 1


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
    paginates it at one quota unit per page. This deliberately avoids the
    100-unit ``search.list`` endpoint for complete channel enumeration.
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
        if not tracker.can_afford(_API_CALL_COST):
            raise YouTubeApiError("YouTube API quota exhausted before channel enumeration")
        try:
            response = await client.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise YouTubeApiError("YouTube API returned a non-object response")
            tracker.record_call(success=True, units=_API_CALL_COST)
            return payload
        except Exception:
            tracker.record_call(success=False, units=_API_CALL_COST)
            raise

    async def _run(client: httpx.AsyncClient) -> tuple[str, list[YouTubeChannelVideo], str | None]:
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
