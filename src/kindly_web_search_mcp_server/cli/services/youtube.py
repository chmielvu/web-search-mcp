from __future__ import annotations

from typing import Any

from ...youtube import search_youtube


class _CliContext:
    async def report_progress(self, **_: Any) -> None:
        return None


async def fetch_youtube_search_payload(
    query: str,
    *,
    num_results: int,
) -> dict[str, Any]:
    results, search_backend = await search_youtube(query, num_results=num_results)
    return {
        "query": query,
        "results": [result.model_dump(exclude_none=True) for result in results],
        "total_results": len(results),
        "search_backend": search_backend,
    }


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

    return await youtube_transcript(
        video_id_or_url,
        language=language,
        translate_to=translate_to,
        output_format=format,  # type: ignore[arg-type]
        backend=backend,
        include_summary=include_summary,
        summary_focus=summary_focus,
        ctx=_CliContext(),  # type: ignore[arg-type]
    )


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
    from ...tools.youtube import youtube_channel_transcription

    response = await youtube_channel_transcription(
        channel,
        max_videos=max_videos,
        language=language,
        translate_to=translate_to,
        output_format=format,  # type: ignore[arg-type]
        backend=backend,
        include_summary=include_summary,
        summary_focus=summary_focus,
        page_token=page_token,
        ctx=_CliContext(),  # type: ignore[arg-type]
    )
    return response.model_dump(exclude_none=True)
