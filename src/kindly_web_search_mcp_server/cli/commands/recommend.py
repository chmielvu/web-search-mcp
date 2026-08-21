from __future__ import annotations

from typing import Annotated

import typer

from ...recommendation import build_command_recommendation
from ..errors import CliError
from ..exit_codes import ExitCode
from ..output import emit_json


def register(app: typer.Typer) -> None:
    @app.command("recommend")
    def recommend_cmd(
        task: Annotated[
            list[str] | None,
            typer.Argument(help="Natural-language task to route to an existing CLI/MCP capability."),
        ] = None,
    ) -> None:
        """Recommend an existing command without executing it."""
        cleaned = " ".join(task or []).strip()
        if not cleaned:
            raise CliError(
                kind="usage_error",
                message="A non-blank task is required.",
                hint="Provide a natural-language task after `web-search-cli recommend`.",
                exit_code=ExitCode.USAGE_ERROR,
                context={"command": "recommend"},
            )
        payload = build_command_recommendation(cleaned)
        emit_json(payload.model_dump(mode="json"), command="recommend")
