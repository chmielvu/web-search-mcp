from __future__ import annotations

import asyncio
from typing import Annotated, Literal

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..output import emit_json


ai_app = typer.Typer(no_args_is_help=True)

@ai_app.command("gemini")
def gemini_cmd(
    query: Annotated[str, typer.Option("--query", help="Search query text.")],
    structured_output: Annotated[
        bool,
        typer.Option("--structured-output/--no-structured-output"),
    ] = False,
    research_goal: Annotated[str | None, typer.Option("--research-goal")] = None,
) -> None:
    from ..services.ai import fetch_gemini_search_payload

    try:
        payload = asyncio.run(
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
    research_goal: Annotated[str, typer.Option("--research-goal")] = "",
    model: Annotated[str | None, typer.Option("--model")] = None,
    num_results: Annotated[int, typer.Option("--num-results")] = 5,
    allowed_domain: Annotated[list[str] | None, typer.Option("--allowed-domain")] = None,
    excluded_domain: Annotated[list[str] | None, typer.Option("--excluded-domain")] = None,
    timeout: Annotated[float | None, typer.Option("--timeout")] = None,
) -> None:
    from ..services.ai import fetch_grok_search_payload

    try:
        payload = asyncio.run(
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
            hint="Check the OpenRouter API key and retry.",
            exit_code=ExitCode.PROVIDER_ERROR,
            context={"command": "ai grok"},
        ) from exc
    emit_json(payload, command="ai grok")


def register(app: typer.Typer) -> None:
    app.add_typer(ai_app, name="ai")

