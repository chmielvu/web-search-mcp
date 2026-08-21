from __future__ import annotations


from typing import Annotated, Any, Literal

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
        typer.Option("--query", help="Alias for --search-query."),
    ] = None,
    objective: Annotated[
        str | None,
        typer.Option(
            "--objective", help="Research goal — what you're trying to accomplish with this search."
        ),
    ] = None,
    research_goal: Annotated[
        str | None,
        typer.Option("--research-goal", help="Alias for --objective."),
    ] = None,
    max_results: Annotated[int | None, typer.Option("--max-results")] = None,
    max_chars_total: Annotated[int | None, typer.Option("--max-chars-total")] = None,
    max_chars_per_result: Annotated[int | None, typer.Option("--max-chars-per-result")] = None,
    client_model: Annotated[str | None, typer.Option("--client-model")] = None,
    session_id: Annotated[str | None, typer.Option("--session-id")] = None,
    include_domain: Annotated[list[str] | None, typer.Option("--include-domain")] = None,
    exclude_domain: Annotated[list[str] | None, typer.Option("--exclude-domain")] = None,
    after_date: Annotated[str | None, typer.Option("--after-date")] = None,
    location: Annotated[str | None, typer.Option("--location")] = None,
    max_age_seconds: Annotated[int | None, typer.Option("--max-age-seconds")] = None,
    timeout_seconds: Annotated[float | None, typer.Option("--timeout-seconds")] = None,
    disable_cache_fallback: Annotated[
        bool, typer.Option("--disable-cache-fallback")
    ] = False,
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
    advanced: dict[str, Any] = {}
    for name, value in (
        ("max_results", max_results),
        ("max_chars_total", max_chars_total),
        ("max_chars_per_result", max_chars_per_result),
        ("client_model", client_model),
        ("session_id", session_id),
        ("include_domains", include_domain),
        ("exclude_domains", exclude_domain),
        ("after_date", after_date),
        ("location", location),
        ("max_age_seconds", max_age_seconds),
        ("timeout_seconds", timeout_seconds),
    ):
        if value is not None:
            advanced[name] = value
    if disable_cache_fallback:
        advanced["disable_cache_fallback"] = True

    try:
        payload = run_cli_async(
            fetch_quick_web_search_payload(queries, goal, **advanced)
        )
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


@search_app.command("inspect")
def inspect_cmd(
    run_key: Annotated[str, typer.Option("--run-key", help="Search run identifier.")],
    db_path: Annotated[str | None, typer.Option("--db-path")] = None,
) -> None:
    """Inspect one search run from the read-only analytics database."""
    from ..services.search_runs import inspect_search_run

    try:
        payload = inspect_search_run(run_key, db_path=db_path)
    except (FileNotFoundError, LookupError) as exc:
        raise CliError(
            kind="not_found",
            message=str(exc),
            hint="Use a run key returned by `search web`.",
            exit_code=ExitCode.NOT_FOUND,
            context={"command": "search inspect", "run_key": run_key},
        ) from exc
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Check the analytics database and retry in read-only mode.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={"command": "search inspect", "run_key": run_key},
        ) from exc
    emit_json(payload, command="search inspect")


@search_app.command("postmortem")
def postmortem_cmd(
    run_key: Annotated[str, typer.Option("--run-key", help="Search run identifier.")],
    db_path: Annotated[str | None, typer.Option("--db-path")] = None,
) -> None:
    """Summarize provider and reranker failures for one search run."""
    from ..services.search_runs import postmortem_search_run

    try:
        payload = postmortem_search_run(run_key, db_path=db_path)
    except (FileNotFoundError, LookupError) as exc:
        raise CliError(
            kind="not_found",
            message=str(exc),
            hint="Provide a run key returned by `search web`.",
            exit_code=ExitCode.NOT_FOUND,
            context={"command": "search postmortem", "run_key": run_key},
        ) from exc
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Check the analytics database and retry in read-only mode.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={"command": "search postmortem", "run_key": run_key},
        ) from exc
    emit_json(payload, command="search postmortem")



@search_app.command("code")
def code_cmd(
    query: Annotated[
        str, typer.Option("--query", help="Code, documentation, or repository search query.")
    ],
    research_goal: Annotated[
        str | None,
        typer.Option(
            "--research-goal", help="Optional task context for query rewriting and reranking."
        ),
    ] = None,
    repository: Annotated[
        list[str] | None,
        typer.Option(
            "--repository", "--repositories", help="GitHub owner/name scope; repeatable (max 25)."
        ),
    ] = None,
    language: Annotated[str | None, typer.Option("--language")] = None,
    path: Annotated[str | None, typer.Option("--path")] = None,
    filename: Annotated[str | None, typer.Option("--filename")] = None,
    extension: Annotated[str | None, typer.Option("--extension")] = None,
    regexp: Annotated[
        bool,
        typer.Option(
            "--regexp/--no-regexp", help="Treat the query as a regular expression where supported."
        ),
    ] = False,
    deep: Annotated[
        bool,
        typer.Option(
            "--deep/--no-deep",
            help="Fetch bounded source windows and broaden repository discovery.",
        ),
    ] = False,
    repo_name: Annotated[str | None, typer.Option("--repo-name")] = None,
    library_name: Annotated[str | None, typer.Option("--library-name")] = None,
    topic: Annotated[str | None, typer.Option("--topic")] = None,
    huggingface_type: Annotated[
        str, typer.Option("--huggingface-type", help="Hub asset type: models, datasets, or both.")
    ] = "both",
    huggingface_sort_by: Annotated[
        str,
        typer.Option(
            "--huggingface-sort-by",
            help="Hub sort: similarity, likes, downloads, trending, or updated.",
        ),
    ] = "similarity",
    huggingface_hybrid: Annotated[
        bool, typer.Option("--huggingface-hybrid/--no-huggingface-hybrid")
    ] = False,
    huggingface_min_likes: Annotated[int, typer.Option("--huggingface-min-likes")] = 0,
    huggingface_min_downloads: Annotated[int, typer.Option("--huggingface-min-downloads")] = 0,
    huggingface_task: Annotated[str | None, typer.Option("--huggingface-task")] = None,
    huggingface_license: Annotated[str | None, typer.Option("--huggingface-license")] = None,
    huggingface_language: Annotated[str | None, typer.Option("--huggingface-language")] = None,
    huggingface_modified_after: Annotated[
        str | None, typer.Option("--huggingface-modified-after")
    ] = None,
    huggingface_min_param_count: Annotated[int, typer.Option("--huggingface-min-param-count")] = 0,
    huggingface_max_param_count: Annotated[
        int | None, typer.Option("--huggingface-max-param-count")
    ] = None,
    mode: Annotated[
        Literal["code", "docs", "discovery", "huggingface"],
        typer.Option("--mode", help="Search mode: code, docs, discovery, or huggingface."),
    ] = "code",
) -> None:
    """Search public code, documentation, GitHub repositories, or Hub assets."""
    from ..services.search_code import fetch_code_search_payload

    if not query.strip():
        raise CliError(
            kind="usage_error",
            message="--query must be a non-blank string.",
            hint="Provide a code, documentation, repository, or Hub asset search query.",
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
                huggingface_type=huggingface_type,
                huggingface_sort_by=huggingface_sort_by,
                huggingface_hybrid=huggingface_hybrid,
                huggingface_min_likes=huggingface_min_likes,
                huggingface_min_downloads=huggingface_min_downloads,
                huggingface_task=huggingface_task,
                huggingface_license=huggingface_license,
                huggingface_language=huggingface_language,
                huggingface_modified_after=huggingface_modified_after,
                huggingface_min_param_count=huggingface_min_param_count,
                huggingface_max_param_count=huggingface_max_param_count,
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


@search_app.command("fetch")
def fetch_cmd(
    repository: Annotated[
        str,
        typer.Option("--repository", help="GitHub owner/name repository."),
    ],
    query: Annotated[str | None, typer.Option("--query")] = None,
    path: Annotated[str | None, typer.Option("--path")] = None,
    symbol: Annotated[str | None, typer.Option("--symbol")] = None,
    ref: Annotated[str | None, typer.Option("--ref", help="Optional git revision (branch, tag, or commit SHA).")] = None,
    regexp: Annotated[
        bool,
        typer.Option("--regexp/--no-regexp", help="Treat --query as a regular expression."),
    ] = False,
    max_matches: Annotated[int, typer.Option("--max-matches")] = 25,
    context_lines: Annotated[int, typer.Option("--context-lines")] = 3,
    start_line: Annotated[int | None, typer.Option("--start-line", help="Optional 1-based start line.")] = None,
    end_line: Annotated[int | None, typer.Option("--end-line", help="Optional 1-based end line.")] = None,
    depth: Annotated[int | None, typer.Option("--depth", help="Optional max directory tree depth.")] = None,
) -> None:
    """Explore a cached GitHub repository snapshot."""
    from ..services.code_fetch import fetch_code_fetch_payload

    try:
        payload = run_cli_async(
            fetch_code_fetch_payload(
                repository,
                query=query,
                path=path,
                symbol=symbol,
                ref=ref,
                regexp=regexp,
                max_matches=max_matches,
                context_lines=context_lines,
                start_line=start_line,
                end_line=end_line,
                depth=depth,
            )
        )
    except ValueError as exc:
        raise CliError(
            kind="usage_error",
            message=str(exc),
            hint="Use a GitHub owner/name repository and valid fetch options.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "search fetch", "repository": repository},
        ) from exc
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Check GITHUB_TOKEN/GH_TOKEN and retry.",
            exit_code=ExitCode.PROVIDER_ERROR,
            context={"command": "search fetch", "repository": repository},
        ) from exc
    emit_json(payload, command="search fetch")



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
