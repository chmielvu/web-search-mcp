from __future__ import annotations

from typing import Annotated, Literal

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..output import emit_json
from ...analytics.formatting import json_safe_rows
from ...analytics.queries import run_analytics_query
from ...analytics.reports import available_reports, run_report


analytics_app = typer.Typer(no_args_is_help=True)


@analytics_app.command("query")
def query_cmd(
    question: Annotated[str, typer.Option("--question", help="Analytics question.")],
    scope: Annotated[Literal["local", "motherduck"], typer.Option("--scope")] = "local",
    max_rows: Annotated[int, typer.Option("--max-rows")] = 100,
    db_path: Annotated[str | None, typer.Option("--db-path")] = None,
) -> None:
    try:
        payload = run_analytics_query(
            question,
            scope=scope,
            max_rows=max_rows,
            db_path=db_path,
        )
    except FileNotFoundError as exc:
        raise CliError(
            kind="not_found",
            message=str(exc),
            hint="Create or point to the analytics DuckDB file and retry.",
            exit_code=ExitCode.NOT_FOUND,
            context={"command": "analytics query"},
        ) from exc
    except ValueError as exc:
        raise CliError(
            kind="usage_error",
            message=str(exc),
            hint="Check the analytics question and retry.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "analytics query"},
        ) from exc
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Check the local analytics database and retry.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={"command": "analytics query"},
        ) from exc
    emit_json(payload, command="analytics query")


@analytics_app.command("report")
def report_cmd(
    report_name: Annotated[
        str,
        typer.Option("--report-name", help="Deterministic report name."),
    ],
    days: Annotated[int, typer.Option("--days")] = 7,
    db_path: Annotated[str | None, typer.Option("--db-path")] = None,
) -> None:
    try:
        table = run_report(report_name, days=days, db_path=db_path)
    except FileNotFoundError as exc:
        raise CliError(
            kind="not_found",
            message=str(exc),
            hint="Create or point to the analytics DuckDB file and retry.",
            exit_code=ExitCode.NOT_FOUND,
            context={"command": "analytics report"},
        ) from exc
    except ValueError as exc:
        raise CliError(
            kind="usage_error",
            message=str(exc),
            hint=f"Available reports: {', '.join(available_reports())}.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "analytics report"},
        ) from exc
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Check the local analytics database and retry.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={"command": "analytics report"},
        ) from exc
    emit_json(
        {
            "report": report_name,
            "days": days,
            "row_count": table.num_rows,
            "rows": json_safe_rows(table.to_pylist()),
            "available_reports": available_reports(),
        },
        command="analytics report",
    )


def register(app: typer.Typer) -> None:
    app.add_typer(analytics_app, name="analytics")
