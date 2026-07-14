from __future__ import annotations


from typing import Annotated

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..output import emit_json
from ..runtime import run_cli_async


links_app = typer.Typer(no_args_is_help=True)


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
