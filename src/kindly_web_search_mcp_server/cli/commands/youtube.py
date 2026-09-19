from __future__ import annotations

from typing import Annotated, Literal

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..outcome import raise_for_payload_error
from ..output import emit_json
from ..runtime import run_cli_async
from ..services.files import write_json_atomic
from ..validation import InputValidationError, validate_safe_path, validate_text_input

youtube_app = typer.Typer(no_args_is_help=True)


def _validated_text(value: str | None, *, field: str, command: str) -> str | None:
    if value is None:
        return None
    try:
        return validate_text_input(value, field=field)
    except InputValidationError as exc:
        raise CliError(
            kind="validation_error",
            message=str(exc),
            hint=f"Pass a plain {field} without control characters or credentials.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": command, "field": field},
        ) from exc


@youtube_app.command("transcript")
def transcript_cmd(
    video_id_or_url: Annotated[
        str,
        typer.Option("--video-id-or-url", help="YouTube URL or video id."),
    ],
    language: Annotated[str | None, typer.Option("--language")] = None,
    translate_to: Annotated[str | None, typer.Option("--translate-to")] = None,
    format: Annotated[
        Literal["text", "timestamped", "json", "markdown"],
        typer.Option("--format"),
    ] = "text",
    backend: Annotated[
        str | None,
        typer.Option("--backend", help="Transcript backend: auto, apify, brightdata."),
    ] = None,
    include_summary: Annotated[
        bool,
        typer.Option("--include-summary", help="Generate a Gemini transcript summary."),
    ] = False,
    summary_focus: Annotated[
        str | None,
        typer.Option("--summary-focus", help="Focus the Gemini summary."),
    ] = None,
) -> None:
    from ..services.youtube import fetch_youtube_transcript_payload

    video_id_or_url = (
        _validated_text(
            video_id_or_url,
            field="--video-id-or-url",
            command="youtube transcript",
        )
        or ""
    )
    language = _validated_text(language, field="--language", command="youtube transcript")
    translate_to = _validated_text(
        translate_to,
        field="--translate-to",
        command="youtube transcript",
    )
    backend = _validated_text(backend, field="--backend", command="youtube transcript")
    summary_focus = _validated_text(
        summary_focus,
        field="--summary-focus",
        command="youtube transcript",
    )

    try:
        payload = run_cli_async(
            fetch_youtube_transcript_payload(
                video_id_or_url,
                language=language,
                translate_to=translate_to,
                format=format,
                backend=backend,
                include_summary=include_summary,
                summary_focus=summary_focus,
            )
        )
    except TimeoutError as exc:
        raise CliError(
            kind="timeout",
            message=f"Transcript fetch timed out for {video_id_or_url}",
            hint="Retry or provide a different video.",
            exit_code=ExitCode.TIMEOUT,
            context={"command": "youtube transcript"},
        ) from exc
    except ValueError as exc:
        raise CliError(
            kind="usage_error",
            message=str(exc),
            hint="Check the YouTube input and retry.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "youtube transcript"},
        ) from exc
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Check the transcript dependencies and retry.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={"command": "youtube transcript"},
        ) from exc
    raise_for_payload_error(
        payload,
        command="youtube transcript",
        hint="Check the video id, transcript availability, and YOUTUBE_* credentials.",
    )
    emit_json(payload, command="youtube transcript")


@youtube_app.command("channel")
def channel_cmd(
    channel: Annotated[str, typer.Option("--channel", help="YouTube channel ID or handle.")],
    max_videos: Annotated[int, typer.Option("--max-videos")] = 20,
    language: Annotated[str | None, typer.Option("--language")] = None,
    translate_to: Annotated[str | None, typer.Option("--translate-to")] = None,
    format: Annotated[
        Literal["text", "timestamped", "json", "markdown"],
        typer.Option("--format"),
    ] = "markdown",
    backend: Annotated[str | None, typer.Option("--backend")] = None,
    include_summary: Annotated[
        bool, typer.Option("--include-summary", help="Generate Gemini summaries.")
    ] = False,
    summary_focus: Annotated[str | None, typer.Option("--summary-focus")] = None,
    page_token: Annotated[str | None, typer.Option("--page-token")] = None,
    output: Annotated[str | None, typer.Option("--output")] = None,
) -> None:
    """Transcribe channel uploads with cache-first partial-failure reporting."""
    from ..services.youtube import fetch_youtube_channel_transcription_payload

    channel = _validated_text(channel, field="--channel", command="youtube channel") or ""
    language = _validated_text(language, field="--language", command="youtube channel")
    translate_to = _validated_text(
        translate_to,
        field="--translate-to",
        command="youtube channel",
    )
    backend = _validated_text(backend, field="--backend", command="youtube channel")
    summary_focus = _validated_text(
        summary_focus,
        field="--summary-focus",
        command="youtube channel",
    )
    page_token = _validated_text(page_token, field="--page-token", command="youtube channel")

    if output is not None:
        try:
            output = validate_safe_path(output, field="--output")
        except InputValidationError as exc:
            raise CliError(
                kind="validation_error",
                message=str(exc),
                hint="Pass --output to a non-secret path without traversal or shell syntax.",
                exit_code=ExitCode.USAGE_ERROR,
                context={"command": "youtube channel", "field": "--output"},
            ) from exc

    try:
        payload = run_cli_async(
            fetch_youtube_channel_transcription_payload(
                channel,
                max_videos=max_videos,
                language=language,
                translate_to=translate_to,
                format=format,
                backend=backend,
                include_summary=include_summary,
                summary_focus=summary_focus,
                page_token=page_token,
            )
        )
    except ValueError as exc:
        raise CliError(
            kind="usage_error",
            message=str(exc),
            hint="Check the channel identifier and transcription options.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "youtube channel", "channel": channel},
        ) from exc
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Check YouTube API credentials and retry with fewer videos.",
            exit_code=ExitCode.PROVIDER_ERROR,
            context={"command": "youtube channel", "channel": channel},
        ) from exc

    if output:
        payload["output_path"] = write_json_atomic(output, payload)
    raise_for_payload_error(
        payload,
        command="youtube channel",
        hint="Check YouTube API credentials and retry with fewer videos.",
    )
    emit_json(payload, command="youtube channel")


def register(app: typer.Typer) -> None:
    app.add_typer(youtube_app, name="youtube")
