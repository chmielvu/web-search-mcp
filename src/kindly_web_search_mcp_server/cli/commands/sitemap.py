from __future__ import annotations


from typing import Annotated

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..output import emit_json
from ..runtime import run_cli_async


sitemap_app = typer.Typer(no_args_is_help=True)


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
) -> None:
    """Generate a sitemap with Tavily Map (no fallback backend)."""
    from ..services.sitemap import fetch_sitemap_payload

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
            )
        )
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
