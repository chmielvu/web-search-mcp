from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..output import emit_json


search_app = typer.Typer(no_args_is_help=True)


@search_app.command("quick")
def quick_cmd(
    query: Annotated[str, typer.Option("--query", help="Search query text.")],
) -> None:
    """Run the Composio/Exa-backed quick web search path."""
    from ..services.quick_search import fetch_quick_web_search_payload

    try:
        payload = asyncio.run(fetch_quick_web_search_payload(query))
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Check the Composio Search credentials and retry.",
            exit_code=ExitCode.PROVIDER_ERROR,
            context={
                "command": "search quick",
                "exception_type": type(exc).__name__,
            },
        ) from exc
    emit_json(payload, command="search quick")


@search_app.command("web")
def web_cmd(
    query: Annotated[str, typer.Option("--query", help="Search query text.")],
    num_results: Annotated[int, typer.Option("--num-results")] = 5,
    rewrite: Annotated[bool, typer.Option("--rewrite/--no-rewrite")] = True,
    research_goal: Annotated[
        str | None,
        typer.Option("--research-goal"),
    ] = None,
    result_offset: Annotated[int, typer.Option("--result-offset")] = 0,
    searxng_category: Annotated[
        list[str] | None,
        typer.Option("--searxng-category"),
    ] = None,
    searxng_engine: Annotated[
        list[str] | None,
        typer.Option("--searxng-engine"),
    ] = None,
    searxng_language: Annotated[
        str | None,
        typer.Option("--searxng-language"),
    ] = None,
    searxng_pageno: Annotated[int, typer.Option("--searxng-pageno")] = 1,
    searxng_time_range: Annotated[
        str | None,
        typer.Option("--searxng-time-range"),
    ] = None,
    searxng_safesearch: Annotated[
        int | None,
        typer.Option("--searxng-safesearch"),
    ] = None,
    site_filter: Annotated[list[str] | None, typer.Option("--site-filter")] = None,
    domain_filter: Annotated[list[str] | None, typer.Option("--domain-filter")] = None,
) -> None:
    """Run the full multi-provider web search pipeline."""
    from ..services.search_web import fetch_web_search_payload

    try:
        payload = asyncio.run(
            fetch_web_search_payload(
                query,
                num_results=num_results,
                rewrite=rewrite,
                research_goal=research_goal,
                result_offset=result_offset,
                searxng_categories=searxng_category,
                searxng_engines=searxng_engine,
                searxng_language=searxng_language,
                searxng_pageno=searxng_pageno,
                searxng_time_range=searxng_time_range,
                searxng_safesearch=searxng_safesearch,
                site_filters=site_filter,
                domain_filters=domain_filter,
            )
        )
    except ValueError as exc:
        raise CliError(
            kind="usage_error",
            message=str(exc),
            hint="Check the search options and retry.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "search web"},
        ) from exc
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Run `web-search-cli doctor` and verify search providers.",
            exit_code=ExitCode.PROVIDER_ERROR,
            context={"command": "search web", "exception_type": type(exc).__name__},
        ) from exc
    emit_json(payload, command="search web")


@search_app.command("academic")
def academic_cmd(
    query: Annotated[str, typer.Option("--query", help="Search query text.")],
    limit: Annotated[int, typer.Option("--limit")] = 5,
    source: Annotated[list[str] | None, typer.Option("--source")] = None,
    year_from: Annotated[int | None, typer.Option("--year-from")] = None,
    year_to: Annotated[int | None, typer.Option("--year-to")] = None,
    field_of_study: Annotated[
        list[str] | None,
        typer.Option("--field-of-study"),
    ] = None,
    venue: Annotated[str | None, typer.Option("--venue")] = None,
    open_access_only: Annotated[
        bool,
        typer.Option("--open-access-only/--no-open-access-only"),
    ] = False,
    sort: Annotated[str, typer.Option("--sort")] = "relevance",
) -> None:
    """Search scholarly sources and return deduplicated papers."""
    from ..services.academic import fetch_academic_search_payload

    try:
        payload = asyncio.run(
            fetch_academic_search_payload(
                query,
                limit=limit,
                sources=source,
                year_from=year_from,
                year_to=year_to,
                fields_of_study=field_of_study,
                venue=venue,
                open_access_only=open_access_only,
                sort=sort,
            )
        )
    except ValueError as exc:
        raise CliError(
            kind="usage_error",
            message=str(exc),
            hint="Check the academic search options and retry.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "search academic"},
        ) from exc
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Run `web-search-cli doctor` and verify scholarly provider access.",
            exit_code=ExitCode.PROVIDER_ERROR,
            context={
                "command": "search academic",
                "exception_type": type(exc).__name__,
            },
        ) from exc
    emit_json(payload, command="search academic")


def register(app: typer.Typer) -> None:
    app.add_typer(search_app, name="search")
