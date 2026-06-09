from __future__ import annotations

import asyncio
from typing import Annotated, Literal

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..output import emit_json


agent_app = typer.Typer(no_args_is_help=True)


@agent_app.command("research")
def research_cmd(
    query: Annotated[str, typer.Option("--query", help="Research query text.")],
    research_goal: Annotated[str | None, typer.Option("--research-goal")] = None,
    session_id: Annotated[str | None, typer.Option("--session-id")] = None,
    depth: Annotated[Literal["quick", "normal", "deep"], typer.Option("--depth")] = "normal",
) -> None:
    from ...agent.models import AgenticResearchRequest  # lazy: langchain.agents ~22s
    from ...agent.runner import run_agentic_web_research

    try:
        payload = asyncio.run(
            run_agentic_web_research(
                AgenticResearchRequest(
                    query=query,
                    research_goal=research_goal,
                    session_id=session_id,
                    depth=depth,
                )
            )
        )
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Check the agent model and tool configuration, then retry.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={"command": "agent research"},
        ) from exc
    emit_json(payload.model_dump(exclude_none=True), command="agent research")


def register(app: typer.Typer) -> None:
    app.add_typer(agent_app, name="agent")

