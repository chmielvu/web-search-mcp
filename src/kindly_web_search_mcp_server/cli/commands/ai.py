from __future__ import annotations

from typing import Annotated

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..output import emit_json
from ..runtime import run_cli_async
from ..validation import InputValidationError, validate_text_input

ai_app = typer.Typer(no_args_is_help=True)


def _validated_text(value: str, *, field: str, command: str) -> str:
    """Reject control characters and credential-shaped values before a provider call."""
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


def _validated_optional(value: str | None, *, field: str, command: str) -> str | None:
    if value is None:
        return None
    return _validated_text(value, field=field, command=command)


def _validated_domains(
    values: list[str] | None,
    *,
    field: str,
    command: str,
) -> list[str] | None:
    if values is None:
        return None
    return [
        _validated_text(value, field=f"{field} #{index}", command=command)
        for index, value in enumerate(values, 1)
    ]


@ai_app.command("gemini")
def gemini_cmd(
    query: Annotated[str, typer.Option("--query", help="Search query text.")],
    structured_output: Annotated[
        bool,
        typer.Option("--structured-output/--no-structured-output"),
    ] = False,
    research_goal: Annotated[
        str,
        typer.Option("--research-goal", help="Required search objective."),
    ] = ...,  # ty: ignore[invalid-parameter-default] # pyright: ignore[reportArgumentType]
) -> None:
    from ..services.ai import fetch_gemini_search_payload

    query = _validated_text(query, field="--query", command="ai gemini")
    research_goal = _validated_text(
        research_goal,
        field="--research-goal",
        command="ai gemini",
    )

    if not query.strip():
        raise CliError(
            kind="usage_error",
            message="Query must be a non-empty string.",
            hint="Provide a search query, for example: --query 'Latest news'.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "ai gemini"},
        )
    if not research_goal.strip():
        raise CliError(
            kind="usage_error",
            message="Research goal must be a non-empty string.",
            hint="Provide a research goal, for example: --research-goal 'Summarize updates'.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "ai gemini"},
        )
    try:
        payload = run_cli_async(
            fetch_gemini_search_payload(
                query,
                structured_output=structured_output,
                research_goal=research_goal,
            )
        )
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Check the Gemini API key and retry.",
            exit_code=ExitCode.PROVIDER_ERROR,
            context={"command": "ai gemini"},
        ) from exc
    emit_json(payload, command="ai gemini")


@ai_app.command("grok")
def grok_cmd(
    query: Annotated[str, typer.Option("--query", help="Search query text.")],
    research_goal: Annotated[
        str,
        typer.Option("--research-goal", help="Required search objective."),
    ] = ...,  # ty: ignore[invalid-parameter-default] # pyright: ignore[reportArgumentType]
    model: Annotated[str | None, typer.Option("--model")] = None,
    num_results: Annotated[int, typer.Option("--num-results")] = 5,
    allowed_domain: Annotated[list[str] | None, typer.Option("--allowed-domain")] = None,
    excluded_domain: Annotated[list[str] | None, typer.Option("--excluded-domain")] = None,
    timeout: Annotated[
        float | None,
        typer.Option("--timeout", help="Override the Grok request timeout (positive seconds)."),
    ] = None,
) -> None:
    from ..services.ai import fetch_grok_search_payload

    query = _validated_text(query, field="--query", command="ai grok")
    research_goal = _validated_text(
        research_goal,
        field="--research-goal",
        command="ai grok",
    )
    model = _validated_optional(model, field="--model", command="ai grok")
    allowed_domain = _validated_domains(
        allowed_domain,
        field="--allowed-domain",
        command="ai grok",
    )
    excluded_domain = _validated_domains(
        excluded_domain,
        field="--excluded-domain",
        command="ai grok",
    )

    if not query.strip():
        raise CliError(
            kind="usage_error",
            message="Query must be a non-empty string.",
            hint="Provide a search query, for example: --query 'Latest news'.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "ai grok"},
        )
    if not research_goal.strip():
        raise CliError(
            kind="usage_error",
            message="Research goal must be a non-empty string.",
            hint="Provide a research goal, for example: --research-goal 'Summarize updates'.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "ai grok"},
        )
    if timeout is not None and timeout <= 0:
        raise CliError(
            kind="usage_error",
            message="--timeout must be a positive number of seconds.",
            hint="Pass --timeout with a value greater than 0, or omit it to use the adapter default.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "ai grok", "timeout": timeout},
        )
    try:
        payload = run_cli_async(
            fetch_grok_search_payload(
                query,
                research_goal=research_goal,
                model=model,
                num_results=num_results,
                allowed_domains=allowed_domain,
                excluded_domains=excluded_domain,
                timeout=timeout,
            )
        )
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Check the XAI_API_KEY and retry.",
            exit_code=ExitCode.PROVIDER_ERROR,
            context={"command": "ai grok"},
        ) from exc
    emit_json(payload, command="ai grok")


def register(app: typer.Typer) -> None:
    app.add_typer(ai_app, name="ai")
