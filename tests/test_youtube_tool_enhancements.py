from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from kindly_web_search_mcp_server.models import YouTubeTranscriptAnalysis
from kindly_web_search_mcp_server.tools.youtube import youtube_transcript


class _Context:
    async def report_progress(self, **_: object) -> None:
        return None


@pytest.mark.asyncio
async def test_youtube_transcript_markdown_includes_summary_and_gliner_analysis() -> None:
    analysis = YouTubeTranscriptAnalysis(
        status="success",
        structured_data={"topics": ["testing"]},
        model_version="fastino/gliner2-multi-v1",
    )
    with (
        patch(
            "kindly_web_search_mcp_server.tools.youtube.fetch_transcript_with_cache",
            return_value=(
                [{"text": "Hello world", "start": 0.0, "duration": 2.0}],
                "cache",
            ),
        ),
        patch(
            "kindly_web_search_mcp_server.content.summary.create_summary",
            new=AsyncMock(
                return_value={
                    "summary": "A short summary.",
                    "key_points": ["Testing matters"],
                }
            ),
        ),
        patch(
            "kindly_web_search_mcp_server.youtube.analysis.analyze_transcript",
            new=AsyncMock(return_value=analysis),
        ),
        patch(
            "kindly_web_search_mcp_server.youtube.yt_dlp_backend.ytdlp_extract_metadata",
            return_value={"title": "Example Video"},
        ),
    ):
        response = await youtube_transcript(
            "dQw4w9WgXcQ",
            output_format="markdown",
            include_summary=True,
            ctx=_Context(),  # type: ignore[arg-type]
        )

    payload = cast(Any, response)
    assert payload["output_format"] == "markdown"
    assert "# YouTube Video Transcript" in payload["transcript_text"]
    assert "A short summary." in payload["transcript_text"]
    assert "GLiNER2 Analysis" in payload["transcript_text"]
    assert payload["analysis"]["status"] == "success"
    assert payload["summary"]["summary"] == "A short summary."
    assert payload["backend_used"] == "cache"
