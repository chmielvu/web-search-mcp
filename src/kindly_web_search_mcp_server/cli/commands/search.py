from __future__ import annotations


from typing import Annotated, Literal

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..output import emit_json
from ..runtime import run_cli_async


search_app = typer.Typer(no_args_is_help=True)


@search_app.command("quick")
def quick_cmd(
    search_query: Annotated[
        list[str] | None,
        typer.Option(
            "--search-query",
            help="Keyword search query (3-6 words). Repeat for 2-3 queries.",
        ),
    ] = None,
    query: Annotated[
        list[str] | None,
        typer.Option(
            "--query", help="Alias for --search-query."
        ),
    ] = None,
    objective: Annotated[
        str | None,
        typer.Option(
            "--objective", help="Research goal — what you're trying to accomplish with this search."
        ),
    ] = None,
    research_goal: Annotated[
        str | None,
        typer.Option(
            "--research-goal", help="Alias for --objective."
        ),
    ] = None,
) -> None:
    """Run the Parallel AI-backed quick web search path."""
    from ..services.quick_search import fetch_quick_web_search_payload

    queries = (search_query or []) + (query or [])
    goal = objective or research_goal or ""
    if not queries:
        raise CliError(
            kind="usage_error",
            message="Either --search-query or --query must be provided.",
            hint="Specify at least one search query string.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "search quick"},
        )
    if not goal:
        raise CliError(
            kind="usage_error",
            message="Either --objective or --research-goal must be provided.",
            hint="Specify an objective or research goal string.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "search quick"},
        )
    try:
        payload = run_cli_async(fetch_quick_web_search_payload(queries, goal))
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Check the PARALLEL_API_KEY setting and retry.",
            exit_code=ExitCode.PROVIDER_ERROR,
            context={
                "command": "search quick",
                "exception_type": type(exc).__name__,
            },
        ) from exc
    emit_json(payload, command="search quick")


@search_app.command("web")
def web_cmd(
    query: Annotated[
        list[str],
        typer.Option(
            "--query",
            "-q",
            help="Search query text (can be specified up to 4 times for multi-query search).",
        ),
    ] = ...,
    rewrite: Annotated[bool, typer.Option("--rewrite/--no-rewrite")] = True,
    research_goal: Annotated[
        str,
        typer.Option("--research-goal", help="Required search objective."),
    ] = ...,
    reranking_instructions: Annotated[
        str | None,
        typer.Option(
            "--reranking-instructions",
            help="Instructions for cross-encoder & LLM rerankers specifying what sites/sources to prioritize or demote.",
        ),
    ] = None,
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
    domain_boost: Annotated[
        list[str] | None,
        typer.Option("--domain-boost", help="Domains to boost (move to front)."),
    ] = None,
    domain_block: Annotated[
        list[str] | None,
        typer.Option("--domain-block", help="Domains to exclude (remove entirely)."),
    ] = None,
    diagnostics: Annotated[
        bool,
        typer.Option("--diagnostics", help="Include full pipeline diagnostics in output."),
    ] = False,
) -> None:
    """Run the full multi-provider web search pipeline."""
    from ..services.search_web import fetch_web_search_payload

    try:
        payload = run_cli_async(
            fetch_web_search_payload(
                query,
                rewrite=rewrite,
                research_goal=research_goal,
                reranking_instructions=reranking_instructions,
                searxng_categories=searxng_category,
                searxng_engines=searxng_engine,
                searxng_language=searxng_language,
                searxng_pageno=searxng_pageno,
                searxng_time_range=searxng_time_range,
                searxng_safesearch=searxng_safesearch,
                site_filters=site_filter,
                domain_filters=domain_filter,
                domain_boost=domain_boost,
                domain_block=domain_block,
                diagnostics=diagnostics,
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


@search_app.command("code")
def code_cmd(
    query: Annotated[str, typer.Option("--query", help="Code, documentation, or repository search query.")],
    research_goal: Annotated[
        str | None,
        typer.Option("--research-goal", help="Optional task context for query rewriting and reranking."),
    ] = None,
    repository: Annotated[
        list[str] | None,
        typer.Option("--repository", "--repositories", help="GitHub owner/name scope; repeatable (max 25)."),
    ] = None,
    language: Annotated[str | None, typer.Option("--language")] = None,
    path: Annotated[str | None, typer.Option("--path")] = None,
    filename: Annotated[str | None, typer.Option("--filename")] = None,
    extension: Annotated[str | None, typer.Option("--extension")] = None,
    regexp: Annotated[
        bool,
        typer.Option("--regexp/--no-regexp", help="Treat the query as a regular expression where supported."),
    ] = False,
    deep: Annotated[
        bool,
        typer.Option("--deep/--no-deep", help="Fetch bounded source windows and broaden repository discovery."),
    ] = False,
    repo_name: Annotated[str | None, typer.Option("--repo-name")] = None,
    library_name: Annotated[str | None, typer.Option("--library-name")] = None,
    topic: Annotated[str | None, typer.Option("--topic")] = None,
    mode: Annotated[
        Literal["code", "docs", "discovery"],
        typer.Option("--mode", help="Search mode: code, docs, or discovery."),
    ] = "code",
) -> None:
    """Search public code, documentation, and GitHub repositories."""
    from ..services.search_code import fetch_code_search_payload
    if not query.strip():
        raise CliError(
            kind="usage_error",
            message="--query must be a non-blank string.",
            hint="Provide a code, documentation, or repository search query.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "search code", "query": query},
        )

    try:
        payload = run_cli_async(
            fetch_code_search_payload(
                query,
                research_goal=research_goal,
                repositories=repository,
                language=language,
                path=path,
                filename=filename,
                extension=extension,
                regexp=regexp,
                deep=deep,
                repo_name=repo_name,
                library_name=library_name,
                topic=topic,
                mode=mode,
            )
        )
    except ValueError as exc:
        raise CliError(
            kind="usage_error",
            message=str(exc),
            hint="Check the code-search options and retry.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "search code"},
        ) from exc
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Run `web-search-cli doctor` and verify code-search providers.",
            exit_code=ExitCode.PROVIDER_ERROR,
            context={"command": "search code", "exception_type": type(exc).__name__},
        ) from exc
    emit_json(payload, command="search code")


@search_app.command("academic")
def academic_cmd(
    query: Annotated[str, typer.Option("--query", help="Search query text.")],
    limit: Annotated[int, typer.Option("--limit")] = 5,
    source: Annotated[list[str] | None, typer.Option("--source")] = None,
    source_type: Annotated[
        str | None,
        typer.Option("--source-type", help="general | polish | archive"),
    ] = None,
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

    if limit < 1:
        raise CliError(
            kind="usage_error",
            message="--limit must be >= 1",
            hint="Use a limit between 1 and 20.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "search academic"},
        )
    limit = min(limit, 20)

    try:
        payload = run_cli_async(
            fetch_academic_search_payload(
                query,
                limit=limit,
                sources=source,
                source_type=source_type,
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
