from __future__ import annotations

import typer

from ..introspection import build_schema_payload
from ..output import emit_json


def register(app: typer.Typer) -> None:
    @app.command("schema")
    def schema_cmd() -> None:
        """Emit the planned CLI command tree as JSON."""
        emit_json(build_schema_payload(app), command="schema")
