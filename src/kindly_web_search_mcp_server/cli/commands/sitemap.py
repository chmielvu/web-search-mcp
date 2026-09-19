from __future__ import annotations

from typing import Annotated

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..output import emit_json
from ..runtime import run_cli_async
from ..validation import InputValidationError, validate_http_url, validate_text_input

sitemap_app = typer.Typer(no_args_is_help=True)


def _validated_text(value: str | None, *, field: str) -> str | None:
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
            context={"command": "sitemap generate", "field": field},
        ) from exc


def _validated_url(value: str, *, field: str) -> str:
    try:
        return validate_http_url(value, field=field)
    except InputValidationError as exc:
        raise CliError(
            kind="validation_error",
            message=str(exc),
            hint="Pass an http(s) URL without credentials or control characters.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "sitemap generate", "field": field},
        ) from exc


def _validated_list(values: list[str] | None, *, field: str) -> list[str] | None:
    if values is None:
        return None
    return [
        _validated_text(value, field=f"{field} #{index}") or ""
        for index, value in enumerate(values, 1)
    ]


@sitemap_app.command("generate")
def generate_cmd(
    url: Annotated[str, typer.Option("--url", help="Website URL to map.")],
    instructions: Annotated[
        str | None,
        typer.Option("--instructions", help="Natural-language guidance for Tavily Map."),
    ] = None,
    max_depth: Annotated[int, typer.Option("--max-depth")] = 1,
    max_breadth: Annotated[int, typer.Option("--max-breadth")] = 20,
    limit: Annotated[int, typer.Option("--limit")] = 50,
    select_paths: Annotated[
        list[str] | None,
        typer.Option("--select-paths", help="Regex path filters. Repeatable."),
    ] = None,
    select_domains: Annotated[
        list[str] | None,
        typer.Option("--select-domains", help="Regex domain filters. Repeatable."),
    ] = None,
    exclude_paths: Annotated[
        list[str] | None,
        typer.Option("--exclude-paths", help="Regex path exclusions. Repeatable."),
    ] = None,
    exclude_domains: Annotated[
        list[str] | None,
        typer.Option("--exclude-domains", help="Regex domain exclusions. Repeatable."),
    ] = None,
    allow_external: Annotated[
        bool,
        typer.Option("--allow-external/--no-allow-external"),
    ] = False,
    timeout: Annotated[
        float | None,
        typer.Option(
            "--timeout",
            help="Override the Tavily Map request timeout (positive seconds).",
        ),
    ] = None,
) -> None:
    """Generate a sitemap with Tavily Map (no fallback backend)."""
    from ..services.sitemap import fetch_sitemap_payload

    if timeout is not None and timeout <= 0:
        raise CliError(
            kind="usage_error",
            message="--timeout must be a positive number of seconds.",
            hint="Pass --timeout with a value greater than 0, or omit it to use the adapter default.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "sitemap generate", "url": url, "timeout": timeout},
        )
    url = _validated_url(url, field="--url")
    instructions = _validated_text(instructions, field="--instructions")
    select_paths = _validated_list(select_paths, field="--select-paths")
    select_domains = _validated_list(select_domains, field="--select-domains")
    exclude_paths = _validated_list(exclude_paths, field="--exclude-paths")
    exclude_domains = _validated_list(exclude_domains, field="--exclude-domains")

    try:
        payload = run_cli_async(
            fetch_sitemap_payload(
                url,
                instructions=instructions,
                max_depth=max_depth,
                max_breadth=max_breadth,
                limit=limit,
                select_paths=select_paths,
                select_domains=select_domains,
                exclude_paths=exclude_paths,
                exclude_domains=exclude_domains,
                allow_external=allow_external,
                timeout=timeout,
            )
        )
    except TimeoutError as exc:
        raise CliError(
            kind="network_error",
            message=str(exc),
            hint="Increase --timeout, retry, or verify Tavily reachability with `web-search-cli doctor`.",
            exit_code=ExitCode.NETWORK_ERROR,
            context={"command": "sitemap generate", "url": url},
        ) from exc
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Check the URL and TAVILY_API_KEY. Run `web-search-cli doctor` to verify.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={
                "command": "sitemap generate",
                "url": url,
                "exception_type": type(exc).__name__,
            },
        ) from exc
    emit_json(payload, command="sitemap generate")


def register(app: typer.Typer) -> None:
    app.add_typer(sitemap_app, name="sitemap")
