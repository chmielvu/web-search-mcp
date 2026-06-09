from __future__ import annotations

import typer

from ..output import emit_json
from ..reference_data import COMMANDS


def register(app: typer.Typer) -> None:
    @app.command("schema")
    def schema_cmd() -> None:
        """Emit the planned CLI command tree as JSON."""
        emit_json(
            {
                "command": "web-search-cli",
                "commands": [{"path": command} for command in COMMANDS],
            },
            command="schema",
        )
