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
from .metadata import build_help_payload, cli_brief, cli_version
from .output import emit_error, emit_json
from .runtime import set_runtime


app = typer.Typer(
    name="web-search-cli",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    add_completion=True,
)


@app.callback()
def global_options(
    ctx: typer.Context,
    agent: Annotated[
        bool,
        typer.Option("--agent", envvar="WEB_SEARCH_CLI_AGENT"),
    ] = True,
    human: Annotated[
        bool,
        typer.Option("--human", envvar="WEB_SEARCH_CLI_HUMAN"),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", envvar="WEB_SEARCH_CLI_QUIET"),
    ] = False,
    profile: Annotated[
        str,
        typer.Option("--profile", envvar="WEB_SEARCH_CLI_PROFILE"),
    ] = "full",
    log_level: Annotated[
        str,
        typer.Option("--log-level", envvar="WEB_SEARCH_CLI_LOG_LEVEL"),
    ] = "error",
    non_interactive: Annotated[
        bool,
        typer.Option("--non-interactive", envvar="WEB_SEARCH_CLI_NON_INTERACTIVE"),
    ] = True,
) -> None:
    """Native JSON-first CLI for web-search-mcp."""
    runtime = set_runtime(
        agent=agent,
        human=human,
        quiet=quiet,
        profile=profile,
        log_level=log_level,
        non_interactive=non_interactive,
    )
    ctx.obj = runtime.as_dict()


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


def _contains_flag(args: list[str], *flags: str) -> bool:
    return any(flag in args for flag in flags)


def _print_special_flags(args: list[str]) -> bool:
    if _contains_flag(args, "--brief"):
        sys.stdout.write(cli_brief() + "\n")
        return True
    if _contains_flag(args, "--version", "-V"):
        sys.stdout.write(cli_version() + "\n")
        return True
    if _contains_flag(args, "--help", "-h"):
        payload = build_help_payload(app, args)
        emit_json(payload, command=f"{payload['command']} --help")
        return True
    return False


def main(args: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if args is None else args)
    if _print_special_flags(args):
        return
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
