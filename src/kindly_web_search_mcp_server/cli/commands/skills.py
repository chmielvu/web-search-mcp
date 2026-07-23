from __future__ import annotations

import sys
from typing import Annotated

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..metadata import skill_catalog
from ..output import emit_json
from ..skill_paths import AGENT_SKILLS_DIR, DEV_SKILL_PATH, USER_SKILL_PATH

skills_app = typer.Typer(no_args_is_help=False)


@skills_app.callback(invoke_without_command=True)
def skills_default(
    ctx: typer.Context,
    name: Annotated[
        str | None,
        typer.Argument(help="Optional skill name to display verbatim markdown."),
    ] = None,
) -> None:
    """List registered agent skills or show full skill markdown by name."""
    if ctx.invoked_subcommand is not None:
        return

    if name:
        target_path = None
        if name == "web-search-cli":
            target_path = USER_SKILL_PATH
        elif name == "web-search-cli-dev":
            target_path = DEV_SKILL_PATH
        elif AGENT_SKILLS_DIR.exists():
            candidate = AGENT_SKILLS_DIR / f"{name}.md"
            if candidate.exists():
                target_path = candidate

        if target_path is None or not target_path.exists():
            available = [s["name"] for s in skill_catalog()]
            raise CliError(
                kind="not_found",
                message=f"Skill '{name}' not found.",
                hint=f"Available skills: {', '.join(available)}",
                exit_code=ExitCode.NOT_FOUND,
                context={"name": name, "available": available},
            )

        sys.stdout.write(target_path.read_text(encoding="utf-8"))
        return

    catalog = skill_catalog()
    emit_json({"skills": catalog, "total": len(catalog)}, command="skills")


def register(app: typer.Typer) -> None:
    app.add_typer(skills_app, name="skills")
