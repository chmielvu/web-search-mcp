from __future__ import annotations

from typing import Annotated, Literal

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..output import emit_json
from ..runtime import run_cli_async
from ..services.files import write_json_atomic, write_text_atomic

content_app = typer.Typer(no_args_is_help=True)


@content_app.command("fetch")
def fetch_cmd(
    url: Annotated[
        list[str] | None, typer.Option("--url", help="URL to fetch; repeat for bulk fetch.")
    ] = None,
    input_file: Annotated[
        str | None,
        typer.Option("--input-file", help="URL lines or JSONL records; use '-' for stdin."),
    ] = None,
    offset: Annotated[int, typer.Option("--offset", min=0)] = 0,
    cursor: Annotated[str | None, typer.Option("--cursor")] = None,
    ai_summary: Annotated[
        bool,
        typer.Option(
            "--ai-summary/--no-ai-summary",
            help="Include a detailed source-grounded Gemini summary.",
        ),
    ] = False,
    focus_query: Annotated[str | None, typer.Option("--focus-query")] = None,
    include_links: Annotated[
        bool,
        typer.Option("--include-links/--no-include-links"),
    ] = False,
    processing_mode: Annotated[
        Literal["agent", "index"],
        typer.Option(
            "--processing-mode",
            help=(
                "agent (default): ephemeral one-shot fetch; index: persist selected "
                "markdown under REPO_ROOT/outputs and surface per-result output_path."
            ),
        ),
    ] = "agent",
    output: Annotated[str | None, typer.Option("--output")] = None,
) -> None:
    """Fetch one or multiple URLs through the unified fetch pipeline."""
    from ..services.content import fetch_payload
    from ..services.input import read_url_inputs

    try:
        input_urls = read_url_inputs(url, input_file)
        payload = run_cli_async(
            fetch_payload(
                urls=input_urls,
                cursor=cursor,
                offset=offset,
                ai_summary=ai_summary,
                focus_query=focus_query,
                include_links=include_links,
                processing_mode=processing_mode,
            )
        )
    except ValueError as exc:
        raise CliError(
            kind="usage_error",
            message=str(exc),
            hint="Provide at least one URL or a valid cursor.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "content fetch"},
        ) from exc
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Run `web-search-cli doctor` and verify fetch dependencies.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={"command": "content fetch", "exception_type": type(exc).__name__},
        ) from exc

    # Index mode persists via finalize_artifact; surface the artifact's output_path
    # alongside the user's --output so callers can distinguish their copy from the
    # canonical finalized artifact written under REPO_ROOT/outputs.
    index_output_paths: list[str] = []
    results = payload.get("results") or []
    for item in results if isinstance(results, list) else []:
        if isinstance(item, dict):
            path = item.get("output_path")
            if isinstance(path, str) and path:
                index_output_paths.append(path)
    if processing_mode == "index" and index_output_paths:
        payload["index_output_paths"] = index_output_paths

    if output:
        if payload.get("mode") == "single" and payload.get("results"):
            content = payload["results"][0].get("content", "")
            if not isinstance(content, str):
                raise CliError(
                    kind="schema_error",
                    message="Fetch response did not contain string content.",
                    hint="Retry without --output and inspect the structured response.",
                    exit_code=ExitCode.SCHEMA_ERROR,
                    context={"command": "content fetch", "output": output},
                )
            payload["output_path"] = write_text_atomic(output, content)
        else:
            payload["output_path"] = write_json_atomic(output, payload)
    emit_json(payload, command="content fetch")


def register(app: typer.Typer) -> None:
    app.add_typer(content_app, name="content")
