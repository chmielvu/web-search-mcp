from __future__ import annotations

import asyncio
from typing import Annotated, Literal

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..output import emit_json
from ..services.youtube import (
    fetch_youtube_search_payload,
    fetch_youtube_transcript_payload,
)


youtube_app = typer.Typer(no_args_is_help=True)


@youtube_app.command("search")
def search_cmd(
    query: Annotated[str, typer.Option("--query", help="Search query text.")],
    num_results: Annotated[int, typer.Option("--num-results")] = 5,
) -> None:
    try:
        payload = asyncio.run(
            fetch_youtube_search_payload(query, num_results=num_results)
        )
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Check the SearXNG configuration and retry.",
            exit_code=ExitCode.PROVIDER_ERROR,
            context={"command": "youtube search"},
        ) from exc
    emit_json(payload, command="youtube search")


@youtube_app.command("transcript")
def transcript_cmd(
    video_id_or_url: Annotated[
        str,
        typer.Option("--video-id-or-url", help="YouTube URL or video id."),
    ],
    language: Annotated[str | None, typer.Option("--language")] = None,
    translate_to: Annotated[str | None, typer.Option("--translate-to")] = None,
    format: Annotated[
        Literal["text", "timestamped", "json"],
        typer.Option("--format"),
    ] = "text",
    backend: Annotated[
        str | None,
        typer.Option("--backend", help="Transcript backend: auto, ytdlp, api."),
    ] = None,
) -> None:
    try:
        payload = asyncio.run(
            fetch_youtube_transcript_payload(
                video_id_or_url,
                language=language,
                translate_to=translate_to,
                format=format,
                backend=backend,
            )
        )
    except asyncio.TimeoutError as exc:
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
    emit_json(payload, command="youtube transcript")


def register(app: typer.Typer) -> None:
    app.add_typer(youtube_app, name="youtube")

