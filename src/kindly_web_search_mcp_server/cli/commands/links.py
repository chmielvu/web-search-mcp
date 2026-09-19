from __future__ import annotations

from typing import Annotated

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..outcome import raise_for_payload_error
from ..output import emit_json
from ..runtime import run_cli_async
from ..validation import InputValidationError, validate_http_url, validate_text_input

links_app = typer.Typer(no_args_is_help=True)


def _validated_url(value: str, *, command: str) -> str:
    try:
        return validate_http_url(value, field="--url")
    except InputValidationError as exc:
        raise CliError(
            kind="validation_error",
            message=str(exc),
            hint="Pass an http(s) URL without credentials or control characters.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": command, "field": "--url"},
        ) from exc


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


def _validated_domains(
    values: list[str] | None,
    *,
    field: str,
    command: str,
) -> list[str] | None:
    if values is None:
        return None
    return [
        _validated_text(value, field=f"{field} #{index}", command=command) or ""
        for index, value in enumerate(values, 1)
    ]


@links_app.command("discover")
def discover_cmd(
    url: Annotated[str, typer.Option("--url", help="Page or sitemap URL.")],
    max_links: Annotated[int, typer.Option("--max-links")] = 100,
    include_external: Annotated[
        bool,
        typer.Option("--include-external/--no-include-external"),
    ] = True,
    same_domain_only: Annotated[
        bool,
        typer.Option("--same-domain-only/--no-same-domain-only"),
    ] = False,
    strip_selectors: Annotated[
        str | None,
        typer.Option("--strip-selectors"),
    ] = None,
) -> None:
    from ..services.link_tools import fetch_discover_links_payload

    url = _validated_url(url, command="links discover")
    strip_selectors = _validated_text(
        strip_selectors,
        field="--strip-selectors",
        command="links discover",
    )

    try:
        payload = run_cli_async(
            fetch_discover_links_payload(
                url,
                max_links=max_links,
                include_external=include_external,
                same_domain_only=same_domain_only,
                strip_selectors=strip_selectors,
            )
        )
    except ValueError as exc:
        raise CliError(
            kind="usage_error",
            message=str(exc),
            hint="Check the URL and retry.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "links discover"},
        ) from exc
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Run `web-search-cli doctor` and verify fetch dependencies.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={"command": "links discover"},
        ) from exc
    raise_for_payload_error(
        payload,
        command="links discover",
        hint="Run `web-search-cli doctor` and verify fetch dependencies.",
    )
    emit_json(payload, command="links discover")


@links_app.command("similar")
def similar_cmd(
    url: Annotated[str, typer.Option("--url", help="Known good URL.")],
    num_results: Annotated[int, typer.Option("--num-results")] = 5,
    search_type: Annotated[str, typer.Option("--search-type")] = "neural",
    category: Annotated[str | None, typer.Option("--category")] = None,
    include_domain: Annotated[list[str] | None, typer.Option("--include-domain")] = None,
    exclude_domain: Annotated[list[str] | None, typer.Option("--exclude-domain")] = None,
) -> None:
    from ..services.link_tools import fetch_similar_links_payload

    url = _validated_url(url, command="links similar")
    search_type = _validated_text(search_type, field="--search-type", command="links similar") or ""
    category = _validated_text(category, field="--category", command="links similar")
    include_domain = _validated_domains(
        include_domain,
        field="--include-domain",
        command="links similar",
    )
    exclude_domain = _validated_domains(
        exclude_domain,
        field="--exclude-domain",
        command="links similar",
    )

    try:
        payload = run_cli_async(
            fetch_similar_links_payload(
                url,
                num_results=num_results,
                search_type=search_type,
                category=category,
                include_domains=include_domain,
                exclude_domains=exclude_domain,
            )
        )
    except ValueError as exc:
        raise CliError(
            kind="usage_error",
            message=str(exc),
            hint="Check the URL and retry.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "links similar"},
        ) from exc
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Check Composio Search access and retry.",
            exit_code=ExitCode.PROVIDER_ERROR,
            context={"command": "links similar"},
        ) from exc
    emit_json(payload, command="links similar")


def register(app: typer.Typer) -> None:
    app.add_typer(links_app, name="links")
