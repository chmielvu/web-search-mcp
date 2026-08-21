from __future__ import annotations


from typing import Annotated

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..output import emit_json
from ..runtime import run_cli_async
from ..services.files import write_json_atomic, write_text_atomic

content_app = typer.Typer(no_args_is_help=True)


@content_app.command("get")
def get_cmd(
    url: Annotated[str, typer.Option("--url", help="URL to fetch.")],
    char_offset: Annotated[int, typer.Option("--char-offset")] = 0,
    char_length: Annotated[int, typer.Option("--char-length")] = 20_000,
    ai_summary: Annotated[
        bool,
        typer.Option(
            "--ai-summary/--no-ai-summary",
            help="Include a detailed source-grounded Gemini summary.",
        ),
    ] = False,
    focus_query: Annotated[
        str | None,
        typer.Option("--focus-query"),
    ] = None,
    include_metadata: Annotated[
        bool,
        typer.Option("--include-metadata/--no-include-metadata"),
    ] = True,
    include_links: Annotated[
        bool,
        typer.Option("--include-links/--no-include-links"),
    ] = False,
    max_links: Annotated[int, typer.Option("--max-links")] = 25,
    strip_selectors: Annotated[
        str | None,
        typer.Option("--strip-selectors"),
    ] = None,
    output: Annotated[str | None, typer.Option("--output")] = None,
) -> None:
    """Fetch one known URL with bounded windowing."""
    from ..services.content import fetch_content_payload

    try:
        payload = run_cli_async(
            fetch_content_payload(
                url,
                char_offset=char_offset,
                char_length=char_length,
                ai_summary=ai_summary,
                focus_query=focus_query,
                include_metadata=include_metadata,
                include_links=include_links,
                max_links=max_links,
                strip_selectors=strip_selectors,
            )
        )
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Run `web-search-cli doctor` and confirm the fetch dependencies and environment variables.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={
                "command": "content get",
                "url": url,
                "exception_type": type(exc).__name__,
            },
        ) from exc
    if output:
        page_content = payload.get("page_content")
        if not isinstance(page_content, str):
            raise CliError(
                kind="schema_error",
                message="Content response did not contain page_content.",
                hint="Retry without --output and inspect the structured response.",
                exit_code=ExitCode.SCHEMA_ERROR,
                context={"command": "content get", "output": output},
            )
        payload["output_path"] = write_text_atomic(output, page_content)
    emit_json(payload, command="content get")


@content_app.command("batch")
def batch_cmd(
    url: Annotated[list[str] | None, typer.Option("--url")] = None,
    input_file: Annotated[
        str | None,
        typer.Option("--input-file", help="URL lines or JSONL records; use '-' for stdin."),
    ] = None,
    cursor: Annotated[str | None, typer.Option("--cursor")] = None,
    max_concurrency: Annotated[int, typer.Option("--max-concurrency")] = 4,
    per_item_char_length: Annotated[
        int,
        typer.Option("--per-item-char-length"),
    ] = 12000,
    total_char_budget: Annotated[
        int,
        typer.Option("--total-char-budget"),
    ] = 120000,
    per_url_timeout_seconds: Annotated[
        float,
        typer.Option("--per-url-timeout-seconds"),
    ] = 120.0,
    ai_summary: Annotated[
        bool,
        typer.Option(
            "--ai-summary/--no-ai-summary",
            help="Include a detailed source-grounded Gemini summary for each item.",
        ),
    ] = False,
    focus_query: Annotated[
        str | None,
        typer.Option("--focus-query"),
    ] = None,
    include_metadata: Annotated[
        bool,
        typer.Option("--include-metadata/--no-include-metadata"),
    ] = True,
    include_links: Annotated[
        bool,
        typer.Option("--include-links/--no-include-links"),
    ] = False,
    max_links: Annotated[int, typer.Option("--max-links")] = 25,
    strip_selectors: Annotated[
        str | None,
        typer.Option("--strip-selectors"),
    ] = None,
    output: Annotated[str | None, typer.Option("--output")] = None,
) -> None:
    """Fetch multiple URLs with a total content budget.

    Use --ai-summary to add a detailed source-grounded Gemini summary to each
    returned item. Use focus_query to bias summaries toward a topic, term, or
    comparison.
    """
    from ..services.content_batch import fetch_batch_content_payload
    from ..services.input import read_url_inputs

    try:
        input_urls = read_url_inputs(url, input_file)
        payload = run_cli_async(
            fetch_batch_content_payload(
                urls=input_urls,
                cursor=cursor,
                max_concurrency=max_concurrency,
                per_item_char_length=per_item_char_length,
                total_char_budget=total_char_budget,
                per_url_timeout_seconds=per_url_timeout_seconds,
                ai_summary=ai_summary,
                focus_query=focus_query,
                include_metadata=include_metadata,
                include_links=include_links,
                max_links=max_links,
                strip_selectors=strip_selectors,
            )
        )
    except ValueError as exc:
        raise CliError(
            kind="usage_error",
            message=str(exc),
            hint="Check the batch content options and retry.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "content batch"},
        ) from exc
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Run `web-search-cli doctor` and verify fetch dependencies.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={"command": "content batch", "exception_type": type(exc).__name__},
        ) from exc
    if output:
        payload["output_path"] = write_json_atomic(output, payload)
    emit_json(payload, command="content batch")


def register(app: typer.Typer) -> None:
    app.add_typer(content_app, name="content")
