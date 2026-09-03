from __future__ import annotations

from unittest.mock import AsyncMock, patch
from typing import Any, cast

import pytest

from kindly_web_search_mcp_server.models import YouTubeChannelVideo, YouTubeTranscriptResponse
from kindly_web_search_mcp_server.tools.youtube import youtube_transcript


class _Context:
    async def report_progress(self, **_: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_channel_transcription_returns_partial_per_video_results() -> None:
    videos = [
        YouTubeChannelVideo(
            video_id="abcdefghijk",
            video_url="https://www.youtube.com/watch?v=abcdefghijk",
            title="One",
        ),
        YouTubeChannelVideo(
            video_id="lmnopqrstuv",
            video_url="https://www.youtube.com/watch?v=lmnopqrstuv",
            title="Two",
        ),
    ]
    success = YouTubeTranscriptResponse(
        video_id="abcdefghijk",
        video_url=videos[0].video_url,
        transcript_text="# transcript",
        language="en",
        backend_used="cache",
        output_format="markdown",
    ).model_dump()

    async def transcript_side_effect(video_id: str, **_: Any) -> dict[str, Any]:
        if video_id == "lmnopqrstuv":
            raise RuntimeError("unavailable")
        return success

    tracker = type("Tracker", (), {"snapshot": lambda self: {"used": 2}})()
    with (
        patch(
            "kindly_web_search_mcp_server.tools.youtube.list_channel_videos",
            new=AsyncMock(return_value=("UC12345678901234567890", videos, None)),
        ),
        patch(
            "kindly_web_search_mcp_server.tools.youtube.youtube_transcript",
            new=AsyncMock(side_effect=transcript_side_effect),
        ),
        patch(
            "kindly_web_search_mcp_server.tools.youtube.get_youtube_api_quota_tracker",
            return_value=tracker,
        ),
    ):
        response = await youtube_transcript(
            "UC12345678901234567890",
            ctx=_Context(),  # type: ignore[arg-type]
        )

    payload = cast(Any, response)
    assert payload.completed_videos == 1
    assert payload.failed_videos == 1
    assert [item.status for item in payload.items] == ["cached", "failed"]
