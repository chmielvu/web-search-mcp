"""YouTube Data API v3 search provider using search.list endpoint.

Uses raw httpx (consistent with google_cse.py pattern) — no heavy
google-api-python-client dependency.

Cost per search: 100 units (search.list) + 1 unit per 50 IDs (videos.list
enrichment, added in Phase 3).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..models import WebSearchResult
from ..settings import settings
from ..search.base_provider import run_provider
from .models import YouTubeApiError
from .api_quota import get_youtube_api_quota_tracker

logger = logging.getLogger(__name__)

_YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
_SEARCH_COST = 100  # units per search.list call


async def search_youtube_api(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Search YouTube via Data API v3 search.list endpoint.

    Cost: 100 quota units per call.

    Args:
        query: Search query string.
        num_results: Maximum number of results (1-50).
        http_client: Optional httpx.AsyncClient for connection reuse.

    Returns:
        List of WebSearchResult objects (title, link, snippet).

    Raises:
        YouTubeApiError: On missing key, quota exhaustion, or API errors.
    """
    if not query.strip():
        return []

    if num_results < 1:
        return []

    num_results = min(num_results, 50)

    api_key = settings.youtube_api_key.strip()
    if not api_key:
        raise YouTubeApiError(
            "GOOGLE_API_KEY is not configured. "
            "YouTube Data API search requires a valid API key."
        )

    # Quota check before making the call
    tracker = get_youtube_api_quota_tracker()
    if not tracker.can_afford(_SEARCH_COST):
        raise YouTubeApiError(
            f"YouTube API daily quota exhausted ({tracker.snapshot()['used']}/"
            f"{tracker.snapshot()['daily_quota']} units used). "
            "Quota resets at midnight Pacific time."
        )

    timeout_seconds = settings.youtube_api_timeout_seconds

    params: dict[str, Any] = {
        "key": api_key,
        "q": query,
        "type": "video",
        "part": "snippet",
        "maxResults": num_results,
        "safeSearch": "moderate",
    }

    # Optional language/region from settings
    language = settings.youtube_api_language.strip()
    if language:
        params["relevanceLanguage"] = language

    region = settings.youtube_api_region.strip()
    if region:
        params["regionCode"] = region

    headers = {
        "Accept": "application/json",
    }

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        resp = await client.get(
            _YOUTUBE_SEARCH_URL,
            params=params,
            headers=headers,
            timeout=timeout_seconds,
        )
        if resp.status_code == 403:
            error_body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            reason = error_body.get("error", {}).get("errors", [{}])[0].get("reason", "quotaExceeded")
            tracker.record_call(success=False, units=_SEARCH_COST)
            raise YouTubeApiError(
                f"YouTube API returned 403 (reason: {reason}). "
                "Check API key validity and quota status."
            )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise YouTubeApiError("YouTube API response was not a JSON object")
        tracker.record_call(success=True, units=_SEARCH_COST)
        return data

    def _parse_response(data: dict[str, Any]) -> list[WebSearchResult]:
        items = data.get("items", [])
        if not isinstance(items, list):
            raise YouTubeApiError("YouTube API response missing 'items' list")

        results: list[WebSearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue

            snippet = item.get("snippet", {})
            video_id_obj = item.get("id", {})
            video_id = (
                video_id_obj.get("videoId", "")
                if isinstance(video_id_obj, dict)
                else ""
            )

            title = snippet.get("title", "")
            description = snippet.get("description", "")
            channel = snippet.get("channelTitle", "")

            if not title or not video_id:
                continue

            link = f"https://www.youtube.com/watch?v={video_id}"

            # Build a richer snippet from description + channel
            rich_snippet = description.strip()
            if channel:
                rich_snippet = f"[{channel}] {rich_snippet}" if rich_snippet else channel

            results.append(
                WebSearchResult(
                    title=title.strip(),
                    link=link,
                    snippet=rich_snippet,
                )
            )

        return results

    results = await run_provider(
        "youtube_api",
        query,
        num_results,
        request=_do_request,
        parse_response=_parse_response,
        http_client=http_client,
        timeout_seconds=timeout_seconds,
    )

    # Phase 3: Enrich results with metadata from videos.list (best-effort)
    if results:
        try:
            from .api_enrichment import enrich_video_metadata, merge_enrichment_into_results
            from .url_parser import extract_video_id

            video_ids = []
            for r in results:
                try:
                    vid = extract_video_id(r.link)
                    video_ids.append(vid)
                except Exception:
                    continue

            if video_ids:
                metadata = await enrich_video_metadata(
                    video_ids, http_client=http_client,
                )
                results = merge_enrichment_into_results(results, metadata)
        except Exception as exc:
            logger.debug("Video enrichment skipped: %s", exc)

    return results
