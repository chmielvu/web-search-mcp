from __future__ import annotations

from typing import Annotated, Literal

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..output import emit_json
from ..validation import InputValidationError, validate_safe_path, validate_text_input

analytics_app = typer.Typer(no_args_is_help=True)


def _validated_text(value: str, *, field: str, command: str) -> str:
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


def _validated_path(value: str | None, *, field: str, command: str) -> str | None:
    if value is None:
        return None
    try:
        return validate_safe_path(value, field=field)
    except InputValidationError as exc:
        raise CliError(
            kind="validation_error",
            message=str(exc),
            hint=f"Pass a non-secret {field} without traversal or shell syntax.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": command, "field": field},
        ) from exc


@analytics_app.command("query")
def query_cmd(
    question: Annotated[str, typer.Option("--question", help="Analytics question.")],
    scope: Annotated[Literal["local", "motherduck"], typer.Option("--scope")] = "local",
    max_rows: Annotated[int, typer.Option("--max-rows")] = 100,
    db_path: Annotated[str | None, typer.Option("--db-path")] = None,
) -> None:
    from ...analytics.queries import run_analytics_query

    question = _validated_text(question, field="--question", command="analytics query")
    db_path = _validated_path(db_path, field="--db-path", command="analytics query")

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
    from ...analytics.formatting import json_safe_rows

    report_name = _validated_text(report_name, field="--report-name", command="analytics report")
    db_path = _validated_path(db_path, field="--db-path", command="analytics report")

    from ...analytics.reports import available_reports, run_report

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
