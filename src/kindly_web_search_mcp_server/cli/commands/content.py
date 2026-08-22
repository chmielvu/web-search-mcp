from __future__ import annotations

from typing import Annotated

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..output import emit_json
from ..runtime import run_cli_async
from ..services.files import write_json_atomic, write_text_atomic

content_app = typer.Typer(no_args_is_help=True)


@content_app.command("fetch")
def fetch_cmd(
    url: Annotated[list[str] | None, typer.Option("--url", help="URL to fetch; repeat for bulk fetch.")] = None,
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
    include_metadata: Annotated[
        bool,
        typer.Option("--include-metadata/--no-include-metadata"),
    ] = True,
    include_links: Annotated[
        bool,
        typer.Option("--include-links/--no-include-links"),
    ] = False,
    max_links: Annotated[int, typer.Option("--max-links", min=1)] = 25,
    strip_selectors: Annotated[str | None, typer.Option("--strip-selectors")] = None,
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

    if output:
        if payload.get("mode") == "single" and payload.get("results"):
            page_content = payload["results"][0].get("page_content", "")
            if not isinstance(page_content, str):
                raise CliError(
                    kind="schema_error",
                    message="Fetch response did not contain string page_content.",
                    hint="Retry without --output and inspect the structured response.",
                    exit_code=ExitCode.SCHEMA_ERROR,
                    context={"command": "content fetch", "output": output},
                )
            payload["output_path"] = write_text_atomic(output, page_content)
        else:
            payload["output_path"] = write_json_atomic(output, payload)
    emit_json(payload, command="content fetch")


def register(app: typer.Typer) -> None:
    app.add_typer(content_app, name="content")
