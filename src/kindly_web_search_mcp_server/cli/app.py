from __future__ import annotations

import sys
from typing import Annotated

import click
import typer

from . import bootstrap  # noqa: F401
from .commands import (
    agent,
    ai,
    analytics,
    content,
    doctor,
    experiments,
    getskill,
    links,
    reference,
    schema,
    search,
    server,
    youtube,
)
from .errors import CliError
from .exit_codes import ExitCode
from .output import emit_error


app = typer.Typer(
    name="web-search-cli",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    add_completion=True,
)


@app.callback()
def global_options(
    agent: Annotated[bool, typer.Option("--agent")] = True,
    human: Annotated[bool, typer.Option("--human")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
    profile: Annotated[str, typer.Option("--profile")] = "full",
    log_level: Annotated[str, typer.Option("--log-level")] = "error",
    non_interactive: Annotated[bool, typer.Option("--non-interactive")] = True,
) -> None:
    """Native JSON-first CLI for web-search-mcp."""
    _ = (agent, human, quiet, profile, log_level, non_interactive)


schema.register(app)
doctor.register(app)
getskill.register(app)
reference.register(app)
search.register(app)
content.register(app)
links.register(app)
ai.register(app)
youtube.register(app)
analytics.register(app)
agent.register(app)
experiments.register(app)
server.register(app)


def main(args: list[str] | None = None) -> None:
    try:
        app(args=args, prog_name="web-search-cli", standalone_mode=False)
    except CliError as exc:
        emit_error(exc.payload())
        raise SystemExit(int(exc.exit_code)) from exc
    except click.ClickException as exc:
        emit_error(
            {
                "error": {
                    "kind": "usage_error",
                    "message": exc.format_message(),
                    "hint": "Run `web-search-cli schema` to inspect valid commands.",
                    "context": {"exception_type": type(exc).__name__},
                }
            }
        )
        raise SystemExit(int(ExitCode.USAGE_ERROR)) from exc
    except Exception as exc:
        emit_error(
            {
                "error": {
                    "kind": "internal_error",
                    "message": str(exc),
                    "hint": "Run `web-search-cli doctor` to inspect CLI readiness.",
                    "context": {"exception_type": type(exc).__name__},
                }
            }
        )
        raise SystemExit(int(ExitCode.INTERNAL_ERROR)) from exc


if __name__ == "__main__":
    main(sys.argv[1:])
