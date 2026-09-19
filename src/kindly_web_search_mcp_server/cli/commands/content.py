from __future__ import annotations

from typing import Annotated, Literal

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..outcome import raise_for_payload_error, raise_if_no_item_succeeded
from ..output import emit_json
from ..runtime import run_cli_async
from ..services.files import write_json_atomic, write_text_atomic
from ..validation import InputValidationError, validate_safe_path, validate_text_input

content_app = typer.Typer(no_args_is_help=True)


def _validated_text(
    value: str | None,
    *,
    field: str,
    command: str,
) -> str | None:
    """Reject control characters and credential-shaped values at the CLI boundary."""
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


def _validated_output(
    value: str | None,
    *,
    command: str,
) -> str | None:
    """Reject traversal, shell syntax, and sensitive output paths."""
    if value is None:
        return None
    try:
        return validate_safe_path(value, field="--output")
    except InputValidationError as exc:
        raise CliError(
            kind="validation_error",
            message=str(exc),
            hint="Pass --output to a non-secret path without traversal or shell syntax.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": command, "field": "--output"},
        ) from exc


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

    cursor = _validated_text(cursor, field="--cursor", command="content fetch")
    focus_query = _validated_text(
        focus_query,
        field="--focus-query",
        command="content fetch",
    )
    output = _validated_output(output, command="content fetch")

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
    raise_if_no_item_succeeded(
        payload,
        command="content fetch",
        hint="Run `web-search-cli doctor` and verify fetch dependencies.",
    )
    emit_json(payload, command="content fetch")


@content_app.command("crawl")
def crawl_cmd(
    url: Annotated[
        list[str] | None,
        typer.Option("--url", help="Seed URL to crawl; repeat for multi-site traversal."),
    ] = None,
    input_file: Annotated[
        str | None,
        typer.Option("--input-file", help="URL lines or JSONL records; use '-' for stdin."),
    ] = None,
    max_depth: Annotated[
        int,
        typer.Option(
            "--max-depth",
            min=0,
            max=2,
            help="Maximum discovered-link depth; 0 processes only the seed URLs.",
        ),
    ] = 0,
    max_pages: Annotated[
        int,
        typer.Option(
            "--max-pages",
            min=1,
            max=100,
            help="Maximum number of attempted pages, including seeds.",
        ),
    ] = 20,
    include_external: Annotated[
        bool,
        typer.Option(
            "--include-external/--no-include-external",
            help="Allow discovered URLs outside the seed sites.",
        ),
    ] = False,
    response_format: Annotated[
        Literal["summary", "detailed"],
        typer.Option(
            "--response-format",
            help="summary omits Markdown and links; detailed includes them.",
        ),
    ] = "summary",
    output: Annotated[str | None, typer.Option("--output")] = None,
) -> None:
    """Crawl bounded public sites through the unified crawl pipeline."""
    from ..services.crawl import fetch_crawl_payload
    from ..services.input import read_url_inputs

    output = _validated_output(output, command="content crawl")

    try:
        input_urls = read_url_inputs(url, input_file)
    except ValueError as exc:
        raise CliError(
            kind="usage_error",
            message=str(exc),
            hint="Provide at least one URL or a valid input file.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "content crawl"},
        ) from exc

    if not input_urls:
        raise CliError(
            kind="usage_error",
            message="crawl requires at least one URL via --url or --input-file.",
            hint="Pass --url https://example.com or pipe URL lines through --input-file -.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "content crawl"},
        )
    if any(not candidate.strip() for candidate in input_urls):
        raise CliError(
            kind="usage_error",
            message="crawl seed URLs must be non-blank.",
            hint="Remove empty entries from --url or --input-file and retry.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "content crawl"},
        )

    try:
        payload = run_cli_async(
            fetch_crawl_payload(
                urls=input_urls,
                max_depth=max_depth,
                max_pages=max_pages,
                include_external=include_external,
                response_format=response_format,
            )
        )
    except ValueError as exc:
        raise CliError(
            kind="usage_error",
            message=str(exc),
            hint="Verify URL shape (https://...) and depth/page ranges (0..2 and 1..100).",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "content crawl"},
        ) from exc
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Run `web-search-cli doctor` and verify crawl dependencies.",
            exit_code=ExitCode.PROVIDER_ERROR,
            context={"command": "content crawl", "exception_type": type(exc).__name__},
        ) from exc

    if output:
        payload["output_path"] = write_json_atomic(output, payload)
    raise_for_payload_error(
        payload,
        command="content crawl",
        hint="Run `web-search-cli doctor` and verify crawl dependencies.",
    )
    raise_if_no_item_succeeded(
        payload,
        command="content crawl",
        hint="Run `web-search-cli doctor` and verify crawl dependencies.",
    )
    emit_json(payload, command="content crawl")


def register(app: typer.Typer) -> None:
    app.add_typer(content_app, name="content")
