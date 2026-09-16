from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from fastmcp.server.context import Context

# The tool accepts exactly these renderings; the CLI passes a plain string.
TranscriptFormat = Literal["text", "timestamped", "json", "markdown"]


class _CliContext:
    async def report_progress(self, **_: Any) -> None:
        return None


async def fetch_youtube_transcript_payload(
    video_id_or_url: str,
    *,
    language: str | None,
    translate_to: str | None,
    format: str,
    backend: str | None = None,
    include_summary: bool = False,
    summary_focus: str | None = None,
) -> dict[str, Any]:
    from ...tools.youtube import youtube_transcript

    response = await youtube_transcript(
        video_id_or_url,
        language=language,
        translate_to=translate_to,
        output_format=cast("TranscriptFormat", format),
        backend=backend,
        include_summary=include_summary,
        summary_focus=summary_focus,
        ctx=cast("Context", _CliContext()),
    )
    return response.model_dump(exclude_none=True)


async def fetch_youtube_channel_transcription_payload(
    channel: str,
    *,
    max_videos: int,
    language: str | None,
    translate_to: str | None,
    format: str,
    backend: str | None,
    include_summary: bool,
    summary_focus: str | None,
    page_token: str | None,
) -> dict[str, Any]:
    from ...tools.youtube import youtube_transcript

    response = await youtube_transcript(
        channel,
        max_videos=max_videos,
        language=language,
        translate_to=translate_to,
        output_format=cast("TranscriptFormat", format),
        backend=backend,
        include_summary=include_summary,
        summary_focus=summary_focus,
        page_token=page_token,
        ctx=cast("Context", _CliContext()),
    )
    return response.model_dump(exclude_none=True)
