from __future__ import annotations

from typing import Annotated, Literal

import typer

from ..output import emit_json
from ..reference_data import EXTERNAL_TOOLS, TOOL_COVERAGE


reference_app = typer.Typer(no_args_is_help=True)


@reference_app.command("tools")
def tools_cmd(
    profile: Annotated[
        Literal["default", "research", "media", "diagnostic", "experimental", "full"],
        typer.Option("--profile"),
    ] = "full",
) -> None:
    """Emit MCP-tool to CLI-command coverage."""
    tools = [
        item for item in TOOL_COVERAGE if profile == "full" or profile in item["profiles"]
    ]
    emit_json({"profile": profile, "tools": tools}, command="reference tools")


@reference_app.command("external-tools")
def external_tools_cmd() -> None:
    """Emit companion CLI tools that should be invoked directly."""
    emit_json({"external_tools": list(EXTERNAL_TOOLS)}, command="reference external-tools")


def register(app: typer.Typer) -> None:
    app.add_typer(reference_app, name="reference")
