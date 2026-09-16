from __future__ import annotations

import asyncio
import logging
import time
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from ..analytics.producers import emit_tool_observability_event
from ..errors import raise_tool_error
from ..models import (
    YouTubeChannelTranscriptionItem,
    YouTubeChannelTranscriptionResponse,
    YouTubeTranscriptResponse,
)
from ..telemetry import record_youtube_transcript
from ..utils.text_clean import clean_text_for_llm
from ..youtube import (
    YouTubeError,
    calculate_total_duration,
    fetch_transcript_with_cache,
    format_transcript_text,
    format_transcript_timestamped,
    list_channel_videos,
    looks_like_channel_target,
    parse_youtube_url,
)
from ..youtube.api_quota import get_youtube_api_quota_tracker

LOGGER = logging.getLogger(__name__)


async def youtube_transcript(
    video_id_or_url: Annotated[
        str, Field(description="YouTube video URL/ID or channel handle/ID/URL (auto-detected).")
    ],
    language: Annotated[
        str | None, Field(description="Preferred transcript language code (e.g. 'en').")
    ] = None,
    translate_to: Annotated[
        str | None, Field(description="Translate the transcript to this language code.")
    ] = None,
    output_format: Annotated[
        Literal["text", "timestamped", "json", "markdown"],
        Field(description="Output rendering: text, timestamped, json, or markdown."),
    ] = "text",
    backend: Annotated[
        str | None,
        Field(description="Override the transcript backend; default comes from settings."),
    ] = None,
    include_summary: Annotated[
        bool, Field(description="Add a source-grounded Gemini summary of the transcript.")
    ] = False,
    summary_focus: Annotated[
        str | None, Field(description="Focus the optional summary on this topic or term.")
    ] = None,
    max_videos: Annotated[
        int, Field(ge=1, description="Channel mode only: max uploads to transcribe per page")
    ] = 20,
    page_token: Annotated[
        str | None, Field(description="Channel mode only: continuation token from next_page_token.")
    ] = None,
    ctx: Context = CurrentContext(),
) -> YouTubeTranscriptResponse | YouTubeChannelTranscriptionResponse:
    """Extract a YouTube transcript, with optional analysis and summary.

    Accepts a video URL/ID or a channel handle/ID/URL. The target is
    auto-detected and determines the response shape.

    WHEN TO USE:
    - Getting the full text of a video found via quick_web_search mode='youtube'.
    - Analyzing entities and relations mentioned in a video.
    - Transcribing a channel's recent uploads in bulk.

    WHEN NOT TO USE:
    - Discovering videos (use quick_web_search mode='youtube' first).

    RETURNS (video mode):
    - transcript_text, transcript_segments, language, is_translated,
      duration_seconds, output_format, and quality diagnostics.
    - analysis: extracted entities and relations when available.
    - summary: a source-grounded summary when include_summary=true.

    RETURNS (channel mode):
    - items[]: per-video results with status (success, cached, failed, or
      skipped), a transcript on success, or an error message.
    - status: ok, partial, or error for the batch.
    - next_page_token: continuation for more uploads; pass it back as
      page_token.

    CHAINING: in channel mode, max_videos (default 20) and page_token control
    pagination. In video mode, use output_format, language, translate_to,
    include_summary, and summary_focus to shape the result.
    """
    if looks_like_channel_target(video_id_or_url):
        try:
            return await _transcribe_channel(
                video_id_or_url,
                language=language,
                translate_to=translate_to,
                output_format=output_format,
                backend=backend,
                include_summary=include_summary,
                summary_focus=summary_focus,
                max_videos=max_videos,
                page_token=page_token,
                ctx=ctx,
            )
        except Exception as e:
            raise_tool_error(e, provider="youtube")
    format = output_format
    from ..content.ai_summary import public_summary, summarize
    from ..settings import settings
    from ..youtube.analysis import analyze_transcript
    from ..youtube.quality import normalize_transcript_segments, truncate_segments
    from ..youtube.transcript import render_youtube_transcript_markdown

    timeout_seconds = settings.youtube_transcript_timeout_seconds
    max_chars = settings.youtube_transcript_max_chars
    effective_backend = backend or settings.youtube_transcript_backend
    started = time.monotonic()
    tool_call_id = str(uuid4())
    emit_tool_observability_event(
        LOGGER,
        "youtube_transcript",
        "request",
        tool_call_id=tool_call_id,
        video_id_or_url=video_id_or_url,
        language=language,
        translate_to=translate_to,
        output_format=format,
        backend=effective_backend,
        include_summary=include_summary,
    )

    try:
        target = parse_youtube_url(video_id_or_url)
        video_id = target.video_id
        canonical_url = target.canonical_url

        await ctx.report_progress(progress=10, total=100, message="Fetching transcript...")
        segments, backend_used = await asyncio.wait_for(
            asyncio.to_thread(
                fetch_transcript_with_cache,
                video_id,
                language=language,
                translate_to=translate_to,
                backend=effective_backend,
            ),
            timeout=timeout_seconds,
        )
        segments, quality = normalize_transcript_segments(segments)
        output_segments, truncated = truncate_segments(segments, max_chars)
        quality = quality.model_copy(update={"truncated": truncated})

        full_text = format_transcript_text(segments)
        await ctx.report_progress(
            progress=40, total=100, message="Running GLiNER2 transcript extraction..."
        )
        analysis = await analyze_transcript(full_text)

        summary_payload: dict[str, Any] | None = None
        if include_summary:
            await ctx.report_progress(
                progress=65, total=100, message="Generating Gemini summary..."
            )
            try:
                summary_payload = await summarize(
                    full_text,
                    ai_summary=True,
                    focus_query=summary_focus,
                    source_urls=None,
                )
            except Exception as exc:
                LOGGER.warning("YouTube summary failed for %s: %s", video_id, exc)
                summary_payload = {
                    "summary": "",
                    "key_points": [],
                    "important_entities": [],
                    "verbatim_terms": [],
                    "limitations": [f"Summary unavailable: {type(exc).__name__}"],
                }
        summary = public_summary(summary_payload)

        metadata: dict[str, object] = {}
        if format == "markdown" or (language is None and not translate_to):
            try:
                from ..youtube.yt_dlp_backend import ytdlp_extract_metadata

                metadata = await asyncio.to_thread(ytdlp_extract_metadata, video_id)
            except Exception:
                metadata = {}

        actual_language = translate_to or language or str(metadata.get("language") or "") or "und"
        is_translated = bool(translate_to)
        if format == "json":
            transcript_text = ""
        elif format == "timestamped":
            transcript_text = format_transcript_timestamped(output_segments)
        elif format == "markdown":
            timestamped = format_transcript_timestamped(output_segments)
            transcript_text = render_youtube_transcript_markdown(
                video_id=video_id,
                title=str(metadata.get("title") or "") or None,
                transcript_text=timestamped,
                language=actual_language,
                is_translated=is_translated,
                source_url=canonical_url,
                duration_seconds=calculate_total_duration(output_segments),
            )
            if summary:
                transcript_text += "\n## Summary\n\n"
                transcript_text += str(summary.get("summary") or "Summary unavailable.") + "\n"
                key_points = summary.get("key_points") or []
                if isinstance(key_points, list) and key_points:
                    transcript_text += "\n### Key points\n\n"
                    transcript_text += "".join(f"- {point}\n" for point in key_points)
            transcript_text += "\n## GLiNER2 Analysis\n\n"
            transcript_text += f"**Status:** `{analysis.status}`\n\n"
            if analysis.entities:
                transcript_text += "### Entities\n\n"
                transcript_text += "| Text | Label | Confidence |\n|---|---|---:|\n"
                transcript_text += "".join(
                    f"| {entity.text.replace('|', '\\\\|')} | {entity.label} | "
                    f"{entity.confidence if entity.confidence is not None else ''} |\n"
                    for entity in analysis.entities
                )
            if analysis.structured_data:
                import json

                transcript_text += "\n### Structured data\n\n```json\n"
                transcript_text += json.dumps(
                    analysis.structured_data, ensure_ascii=False, indent=2
                )
                transcript_text += "\n```\n"
        else:
            transcript_text = format_transcript_text(output_segments)

        if transcript_text and format != "markdown":
            transcript_text = clean_text_for_llm(transcript_text, role="transcript")

        duration_seconds = calculate_total_duration(segments)
        record_youtube_transcript(
            format=format,
            language=actual_language,
            is_translated=is_translated,
            duration_seconds=int(duration_seconds),
            backend_used=backend_used,
        )

        response = YouTubeTranscriptResponse(
            video_id=video_id,
            video_url=canonical_url,
            title=str(metadata.get("title") or "") or None,
            transcript_text=transcript_text,
            language=actual_language,
            is_translated=is_translated,
            duration_seconds=duration_seconds,
            transcript_segments=output_segments if format == "json" else None,
            backend_used=backend_used,
            output_format=format,
            summary=summary,
            analysis=analysis,
            quality=quality,
            error=None,
        ).model_dump(exclude_none=True)

        emit_tool_observability_event(
            LOGGER,
            "youtube_transcript",
            "response",
            tool_call_id=tool_call_id,
            video_id=video_id,
            video_url=canonical_url,
            language=actual_language,
            output_format=format,
            backend=backend_used,
            transcript_text=transcript_text,
            transcript_segments=output_segments if format == "json" else None,
            analysis_status=analysis.status,
            summary_included=include_summary,
            output_count=len(output_segments),
            duration_ms=(time.monotonic() - started) * 1000,
        )
        await ctx.report_progress(progress=100, total=100, message="Done")
        return response  # type: ignore[return-value]

    except TimeoutError:
        record_youtube_transcript(
            format=format,
            language=language or "en",
            is_translated=bool(translate_to),
            duration_seconds=None,
            backend_used=effective_backend,
        )
        error_msg = f"Transcript fetch timed out after {timeout_seconds}s"
        emit_tool_observability_event(
            LOGGER,
            "youtube_transcript",
            "error",
            tool_call_id=tool_call_id,
            video_id_or_url=video_id_or_url,
            backend=effective_backend,
            error_type="timeout",
            error_message=error_msg,
            duration_ms=(time.monotonic() - started) * 1000,
        )
        raise_tool_error(
            TimeoutError(error_msg),
            provider="youtube",
        )

    except YouTubeError as e:
        record_youtube_transcript(
            format=format,
            language=language or "en",
            is_translated=bool(translate_to),
            duration_seconds=None,
            backend_used=effective_backend,
        )
        emit_tool_observability_event(
            LOGGER,
            "youtube_transcript",
            "error",
            tool_call_id=tool_call_id,
            video_id_or_url=video_id_or_url,
            backend=effective_backend,
            error_type="content",
            error_message=str(e),
            duration_ms=(time.monotonic() - started) * 1000,
        )
        raise_tool_error(e, provider="youtube")

    except Exception as e:
        record_youtube_transcript(
            format=format,
            language=language or "en",
            is_translated=bool(translate_to),
            duration_seconds=None,
            backend_used=effective_backend,
        )
        LOGGER.warning("YouTube transcript unexpected error: %s", e)
        emit_tool_observability_event(
            LOGGER,
            "youtube_transcript",
            "error",
            tool_call_id=tool_call_id,
            video_id_or_url=video_id_or_url,
            backend=effective_backend,
            error_type=type(e).__name__,
            error_message=str(e),
            duration_ms=(time.monotonic() - started) * 1000,
        )
        raise_tool_error(e, provider="youtube")


async def _transcribe_channel(
    channel: str,
    *,
    language: str | None,
    translate_to: str | None,
    output_format: Literal["text", "timestamped", "json", "markdown"],
    backend: str | None,
    include_summary: bool,
    summary_focus: str | None,
    max_videos: int,
    page_token: str | None,
    ctx: Context,
) -> YouTubeChannelTranscriptionResponse:
    """Transcribe channel uploads with cache-first partial-failure reporting.

    Called from ``youtube_transcript`` when the target resolves to a channel;
    GLiNER2 analysis is always performed per video by ``youtube_transcript``.
    """
    max_videos = max(1, min(max_videos, 5000))
    channel_id, videos, next_page_token = await list_channel_videos(
        channel,
        max_results=max_videos,
        page_token=page_token,
    )
    items: list[YouTubeChannelTranscriptionItem] = []
    for index, video in enumerate(videos, start=1):
        await ctx.report_progress(
            progress=index - 1,
            total=len(videos),
            message=f"Transcribing {index}/{len(videos)}: {video.title[:80]}",
        )
        try:
            payload = await youtube_transcript(
                video.video_id,
                language=language,
                translate_to=translate_to,
                output_format=output_format,
                backend=backend,
                include_summary=include_summary,
                summary_focus=summary_focus,
                ctx=ctx,
            )
            transcript = YouTubeTranscriptResponse.model_validate(payload)
            status = "cached" if transcript.backend_used == "cache" else "success"
            items.append(
                YouTubeChannelTranscriptionItem(
                    video=video,
                    status=status,
                    transcript=transcript,
                )
            )
        except Exception as exc:
            LOGGER.warning("Channel transcript failed for %s: %s", video.video_id, exc)
            items.append(
                YouTubeChannelTranscriptionItem(
                    video=video,
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

    completed = sum(item.status in {"success", "cached"} for item in items)
    failed = sum(item.status == "failed" for item in items)
    await ctx.report_progress(
        progress=len(videos), total=len(videos), message="Channel transcription complete"
    )
    if completed == 0 and failed > 0:
        status = "error"
    elif (0 < completed < len(videos)) or (failed > 0 and completed > 0):
        status = "partial"
    else:
        status = "ok"
    return YouTubeChannelTranscriptionResponse(
        channel_id=channel_id,
        total_videos=len(videos),
        completed_videos=completed,
        failed_videos=failed,
        items=items,
        next_page_token=next_page_token,
        quota=get_youtube_api_quota_tracker().snapshot(),
        status=status,
    )
