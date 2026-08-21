from __future__ import annotations

from typing import Annotated

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..output import emit_json
from ..services.results import RESULT_KINDS, search_results


results_app = typer.Typer(no_args_is_help=True)


@results_app.command("search")
def search_cmd(
    query: Annotated[
        str | None,
        typer.Option("--query", "-s", help="Text to search across stored result payloads."),
    ] = None,
    result_kind: Annotated[
        str | None,
        typer.Option("--kind", help="Filter: mcp, cli, or deep_research."),
    ] = None,
    source: Annotated[
        str | None,
        typer.Option("--source", help="Exact MCP tool or CLI command filter."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=200)] = 50,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Override WEB_SEARCH_CLI_JOBS_DB for this query."),
    ] = None,
) -> None:
    """Search retained MCP, CLI, and deep-research results."""
    if result_kind is not None and result_kind not in RESULT_KINDS:
        allowed = ", ".join(sorted(RESULT_KINDS))
        raise CliError(
            kind="usage_error",
            message=f"Unsupported --kind {result_kind!r}; expected one of: {allowed}.",
            hint="Use --kind mcp, --kind cli, or --kind deep_research.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "results search"},
        )
    try:
        results = search_results(
            query or "",
            result_kind=result_kind,
            source=source,
            limit=limit,
            db_path=db_path,
        )
    except Exception as exc:
        raise CliError(
            kind="storage_error",
            message=str(exc),
            hint="Check the SQLite path and retry.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={"command": "results search", "db_path": db_path},
        ) from exc

    emit_json(
        {
            "results": results,
            "total": len(results),
            "query": query or "",
            "result_kind": result_kind,
            "source": source,
        },
        command="results search",
    )


def register(app: typer.Typer) -> None:
    app.add_typer(results_app, name="results")
