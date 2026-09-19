from __future__ import annotations

from typing import Annotated, Any, Literal

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..output import emit_json
from ..runtime import run_cli_async
from ..validation import (
    InputValidationError,
    validate_http_url,
    validate_safe_path,
    validate_text_input,
)

search_app = typer.Typer(no_args_is_help=True)


def _validate_query_list(
    queries: list[str] | None,
    *,
    command: str,
    field: str = "--query",
) -> list[str] | None:
    """Validate every entry of a repeated text option."""
    cleaned: list[str] = []
    for index, item in enumerate(queries or [], start=1):
        if not item or not item.strip():
            continue
        try:
            cleaned.append(validate_text_input(item, field=f"{field} #{index}"))
        except InputValidationError as exc:
            raise CliError(
                kind="validation_error",
                message=str(exc),
                hint=(
                    f"Strip control characters and never paste API keys into {field}. "
                    "Pass credentials via environment variables."
                ),
                exit_code=ExitCode.USAGE_ERROR,
                context={"command": command, "field": f"{field} #{index}"},
            ) from exc
    return cleaned or None


def _validate_text_field(
    value: str | None,
    *,
    field: str,
    command: str,
    hint: str,
) -> str | None:
    """Validate a single optional text field and surface a structured error."""
    if value is None:
        return None
    try:
        return validate_text_input(value, field=field)
    except InputValidationError as exc:
        raise CliError(
            kind="validation_error",
            message=str(exc),
            hint=hint,
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": command, "field": field},
        ) from exc


def _validate_path_field(
    value: str | None,
    *,
    field: str,
    command: str,
) -> str | None:
    """Validate a filesystem path option before opening the database."""
    if value is None:
        return None
    try:
        return validate_safe_path(value, field=field)
    except InputValidationError as exc:
        raise CliError(
            kind="validation_error",
            message=str(exc),
            hint=f"Pass a non-secret {field} without traversal or shell metacharacters.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": command, "field": field},
        ) from exc


@search_app.command("quick")
def quick_cmd(
    mode: Annotated[
        Literal["web", "youtube", "docs"],
        typer.Option("--mode", help="Discovery mode: web, youtube, or docs."),
    ] = "web",
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
            "--query", help="Alias for --search-query (web), or video search term (youtube)."
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
        typer.Option("--research-goal", help="Alias for --objective."),
    ] = None,
    repo_url: Annotated[
        str | None,
        typer.Option("--repo-url", help="Public GitHub repository URL (docs mode)."),
    ] = None,
    question: Annotated[
        str | None,
        typer.Option("--question", help="Documentation question (docs mode)."),
    ] = None,
    context7_library_id: Annotated[
        str | None,
        typer.Option("--context7-library-id", help="Explicit Context7 library ID (docs mode)."),
    ] = None,
    num_results: Annotated[int | None, typer.Option("--num-results")] = None,
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
    disable_cache_fallback: Annotated[bool, typer.Option("--disable-cache-fallback")] = False,
) -> None:
    """Run the quick discovery path (web, YouTube, or library docs)."""
    from ..services.quick_search import (
        fetch_quick_docs_payload,
        fetch_quick_web_search_payload,
        fetch_quick_youtube_payload,
    )

    if mode == "youtube":
        term = (query or [None])[0]
        if not term or not term.strip():
            raise CliError(
                kind="usage_error",
                message="--query must be provided in youtube mode.",
                hint="Pass --query 'search terms' with --mode youtube.",
                exit_code=ExitCode.USAGE_ERROR,
                context={"command": "search quick"},
            )
        try:
            term = validate_text_input(term, field="--query")
        except InputValidationError as exc:
            raise CliError(
                kind="validation_error",
                message=str(exc),
                hint="Strip control characters and never paste API keys into --query.",
                exit_code=ExitCode.USAGE_ERROR,
                context={"command": "search quick", "field": "--query"},
            ) from exc
        try:
            payload = run_cli_async(
                fetch_quick_youtube_payload(
                    term,
                    num_results=num_results,
                    max_chars_total=max_chars_total,
                    timeout_seconds=timeout_seconds,
                )
            )
        except Exception as exc:
            raise CliError(
                kind="tool_error",
                message=str(exc),
                hint="Check GOOGLE_API_KEY or SEARXNG_BASE_URL and retry.",
                exit_code=ExitCode.PROVIDER_ERROR,
                context={"command": "search quick", "exception_type": type(exc).__name__},
            ) from exc
        emit_json(payload, command="search quick")
        return

    if mode == "docs":
        if not repo_url or not repo_url.strip():
            raise CliError(
                kind="usage_error",
                message="--repo-url must be provided in docs mode.",
                hint="Pass --repo-url https://github.com/owner/repo with --mode docs.",
                exit_code=ExitCode.USAGE_ERROR,
                context={"command": "search quick"},
            )
        if not question or not question.strip():
            raise CliError(
                kind="usage_error",
                message="--question must be provided in docs mode.",
                hint="Pass --question '...' with --mode docs.",
                exit_code=ExitCode.USAGE_ERROR,
                context={"command": "search quick"},
            )
        try:
            repo_url = validate_http_url(repo_url, field="--repo-url")
            question = validate_text_input(question, field="--question")
        except InputValidationError as exc:
            raise CliError(
                kind="validation_error",
                message=str(exc),
                hint="Strip control characters and never paste API keys into "
                "--repo-url or --question.",
                exit_code=ExitCode.USAGE_ERROR,
                context={"command": "search quick", "field": "--repo-url/--question"},
            ) from exc
        try:
            payload = run_cli_async(
                fetch_quick_docs_payload(
                    repo_url,
                    question,
                    context7_library_id=context7_library_id,
                    max_results=max_results,
                    max_chars_total=max_chars_total,
                    timeout_seconds=timeout_seconds,
                )
            )
        except Exception as exc:
            raise CliError(
                kind="tool_error",
                message=str(exc),
                hint="Check the repository URL and provider connectivity, then retry.",
                exit_code=ExitCode.PROVIDER_ERROR,
                context={"command": "search quick", "exception_type": type(exc).__name__},
            ) from exc
        emit_json(payload, command="search quick")
        return

    raw_queries = (search_query or []) + (query or [])
    queries = _validate_query_list(raw_queries, command="search quick")
    goal = (
        _validate_text_field(
            objective or research_goal,
            field="--objective/--research-goal",
            command="search quick",
            hint="Strip control characters and never paste API keys into "
            "--objective or --research-goal.",
        )
        or ""
    )
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
    include_domain = _validate_query_list(
        include_domain,
        command="search quick",
        field="--include-domain",
    )
    exclude_domain = _validate_query_list(
        exclude_domain,
        command="search quick",
        field="--exclude-domain",
    )
    after_date = _validate_text_field(
        after_date,
        field="--after-date",
        command="search quick",
        hint="Use an ISO date (YYYY-MM-DD) without control characters.",
    )
    client_model = _validate_text_field(
        client_model,
        field="--client-model",
        command="search quick",
        hint="Pass a model identifier without control characters or credentials.",
    )
    session_id = _validate_text_field(
        session_id,
        field="--session-id",
        command="search quick",
        hint="Pass a session identifier without control characters or credentials.",
    )
    location = _validate_text_field(
        location,
        field="--location",
        command="search quick",
        hint="Pass a location without control characters or credentials.",
    )
    advanced: dict[str, Any] = {}
    for name, value in (
        ("max_results", max_results),
        ("max_chars_total", max_chars_total),
        ("max_chars_per_result", max_chars_per_result),
        ("client_model", client_model),
        ("session_id", session_id),
        ("include_domains", include_domain or None),
        ("exclude_domains", exclude_domain or None),
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
        payload = run_cli_async(fetch_quick_web_search_payload(queries, goal, **advanced))
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
    ] = [],
    rewrite: Annotated[
        bool,
        typer.Option(
            "--rewrite/--no-rewrite",
            help="Rewrite the query for broader recall in the first wave (default true). "
            "Does not disable adaptive follow-up waves.",
        ),
    ] = True,
    research_goal: Annotated[
        str,
        typer.Option("--research-goal", help="Required search objective."),
    ] = ...,  # ty: ignore[invalid-parameter-default] - Typer's required-option form  # pyright: ignore[reportArgumentType]
    reranking_instructions: Annotated[
        str | None,
        typer.Option(
            "--reranking-instructions",
            help="Instructions for cross-encoder & LLM rerankers specifying what sites/sources to prioritize or demote.",
        ),
    ] = None,
    date_range: Annotated[
        str | None,
        typer.Option("--date-range", help="Relative freshness: day, week, month, or year."),
    ] = None,
    after_date: Annotated[
        str | None,
        typer.Option("--after-date", help="Only results published on/after YYYY-MM-DD."),
    ] = None,
    before_date: Annotated[
        str | None,
        typer.Option("--before-date", help="Only results published on/before YYYY-MM-DD."),
    ] = None,
    language: Annotated[
        str | None,
        typer.Option("--language", help="ISO 639-1 code (e.g. en, pl) or BCP-47 tag."),
    ] = None,
    region: Annotated[
        str | None,
        typer.Option("--region", help="ISO 3166-1 alpha-2 country bias/filter."),
    ] = None,
    include_undated: Annotated[
        bool | None,
        typer.Option(
            "--include-undated/--exclude-undated",
            help="Undated-result policy under absolute date windows "
            "(default: drop only from providers without native date support).",
        ),
    ] = None,
    domain_boost: Annotated[
        list[str] | None,
        typer.Option("--domain-boost", help="Domains to boost (move to front)."),
    ] = None,
    diagnostics: Annotated[
        bool,
        typer.Option("--diagnostics", help="Include full pipeline diagnostics in output."),
    ] = False,
    cursor: Annotated[
        str | None,
        typer.Option("--cursor", help="Leftover continuation cursor from this run."),
    ] = None,
) -> None:
    """Run the bounded adaptive multi-provider web search pipeline."""
    from ..services.search_web import fetch_web_search_payload

    query = _validate_query_list(query, command="search web") or []
    research_goal = (
        _validate_text_field(
            research_goal,
            field="--research-goal",
            command="search web",
            hint="Pass a non-blank research goal without control characters or credentials.",
        )
        or ""
    )
    reranking_instructions = _validate_text_field(
        reranking_instructions,
        field="--reranking-instructions",
        command="search web",
        hint="Pass reranking instructions without control characters or credentials.",
    )
    date_range = _validate_text_field(
        date_range,
        field="--date-range",
        command="search web",
        hint="Use day, week, month, or year without control characters.",
    )
    after_date = _validate_text_field(
        after_date,
        field="--after-date",
        command="search web",
        hint="Use an ISO date (YYYY-MM-DD) without control characters.",
    )
    before_date = _validate_text_field(
        before_date,
        field="--before-date",
        command="search web",
        hint="Use an ISO date (YYYY-MM-DD) without control characters.",
    )
    language = _validate_text_field(
        language,
        field="--language",
        command="search web",
        hint="Use an ISO language code without control characters.",
    )
    region = _validate_text_field(
        region,
        field="--region",
        command="search web",
        hint="Use an ISO region code without control characters.",
    )
    domain_boost = _validate_query_list(
        domain_boost,
        command="search web",
        field="--domain-boost",
    )
    cursor = _validate_text_field(
        cursor,
        field="--cursor",
        command="search web",
        hint="Pass the continuation cursor returned by a prior search.",
    )
    if not research_goal.strip():
        raise CliError(
            kind="usage_error",
            message="--research-goal must be a non-blank string.",
            hint="Provide the objective that should guide the search.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "search web", "field": "--research-goal"},
        )

    if not (cursor and cursor.strip()) and not any(item.strip() for item in query):
        raise CliError(
            kind="usage_error",
            message="Provide --query or --cursor.",
            hint="Specify at least one search query or a leftover cursor.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "search web"},
        )

    try:
        payload = run_cli_async(
            fetch_web_search_payload(
                query,
                rewrite=rewrite,
                research_goal=research_goal,
                reranking_instructions=reranking_instructions,
                domain_boost=domain_boost,
                date_range=date_range,
                after_date=after_date,
                before_date=before_date,
                language=language,
                region=region,
                include_undated=include_undated,
                diagnostics=diagnostics,
                cursor=cursor,
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

    run_key = (
        _validate_text_field(
            run_key,
            field="--run-key",
            command="search inspect",
            hint="Pass a run key returned by `search web`.",
        )
        or ""
    )
    db_path = _validate_path_field(
        db_path,
        field="--db-path",
        command="search inspect",
    )

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

    run_key = (
        _validate_text_field(
            run_key,
            field="--run-key",
            command="search postmortem",
            hint="Pass a run key returned by `search web`.",
        )
        or ""
    )
    db_path = _validate_path_field(
        db_path,
        field="--db-path",
        command="search postmortem",
    )

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
    cited_by: Annotated[
        str | None,
        typer.Option("--cited-by", help="Papers citing this ID (DOI/arXiv/OpenAlex/S2)."),
    ] = None,
    references: Annotated[
        str | None,
        typer.Option("--references", help="Bibliography of this ID."),
    ] = None,
    author_id: Annotated[
        str | None,
        typer.Option("--author-id", help="OpenAlex author ID or ORCID."),
    ] = None,
) -> None:
    """Search scholarly sources and return deduplicated papers."""
    from ..services.academic import fetch_academic_search_payload

    query = (
        _validate_text_field(
            query,
            field="--query",
            command="search academic",
            hint="Pass a non-blank scholarly query without control characters or credentials.",
        )
        or ""
    )
    source = _validate_query_list(source, command="search academic", field="--source")
    source_type = _validate_text_field(
        source_type,
        field="--source-type",
        command="search academic",
        hint="Use a supported source type without control characters.",
    )
    field_of_study = _validate_query_list(
        field_of_study,
        command="search academic",
        field="--field-of-study",
    )
    venue = _validate_text_field(
        venue,
        field="--venue",
        command="search academic",
        hint="Pass a venue without control characters or credentials.",
    )
    sort = (
        _validate_text_field(
            sort,
            field="--sort",
            command="search academic",
            hint="Use a supported sort value without control characters.",
        )
        or "relevance"
    )
    cited_by = _validate_text_field(
        cited_by,
        field="--cited-by",
        command="search academic",
        hint="Pass a paper identifier without control characters or credentials.",
    )
    references = _validate_text_field(
        references,
        field="--references",
        command="search academic",
        hint="Pass a paper identifier without control characters or credentials.",
    )
    author_id = _validate_text_field(
        author_id,
        field="--author-id",
        command="search academic",
        hint="Pass an author identifier without control characters or credentials.",
    )
    if not query.strip():
        raise CliError(
            kind="usage_error",
            message="--query must be a non-blank string.",
            hint="Provide a scholarly search query.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "search academic", "field": "--query"},
        )

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
                cited_by_paper_id=cited_by,
                references_paper_id=references,
                author_id=author_id,
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
