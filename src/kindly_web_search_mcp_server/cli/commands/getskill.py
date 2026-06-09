from __future__ import annotations

import sys

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..skill_paths import skill_path


def register(app: typer.Typer) -> None:
    @app.command("getskill")
    def getskill_cmd(
        dev: bool = typer.Option(False, "--dev", help="Print the developer skill."),
    ) -> None:
        """Print the bundled CLI skill markdown verbatim."""
        path = skill_path(dev=dev)
        if not path.exists():
            raise CliError(
                kind="not_found",
                message=f"Skill file is missing: {path}",
                hint="Restore the root skills/web-search-cli*/SKILL.md files.",
                exit_code=ExitCode.NOT_FOUND,
                context={"path": str(path), "dev": dev},
            )
        sys.stdout.write(path.read_text(encoding="utf-8"))
