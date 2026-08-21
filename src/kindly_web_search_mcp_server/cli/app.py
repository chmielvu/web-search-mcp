from __future__ import annotations

import logging
import sys
from typing import Annotated

import click
import typer

from . import bootstrap  # noqa: F401
from .commands import (
    ai,
    analytics,
    content,
    doctor,
    experiments,
    getskill,
    feedback,
    inference,
    links,
    recommend,
    reference,
    schema,
    search,
    server,
    sitemap,
    skills,
    youtube,
)
from .errors import CliError, match_hint_rule
from .exit_codes import ExitCode
from .output import emit_error, emit_json
from .metadata import build_full_help_payload, cli_brief, cli_version, command_path_tokens
from .runtime import set_runtime
from ..utils.logging import configure_logging


app = typer.Typer(
    name="web-search-cli",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    add_completion=True,
)


@app.callback()
def global_options(
    ctx: typer.Context,
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
    log_format: Annotated[
        str,
        typer.Option("--log-format", envvar="WEB_SEARCH_CLI_LOG_FORMAT"),
    ] = "text",
    debug: Annotated[
        bool,
        typer.Option("--debug", envvar="WEB_SEARCH_CLI_DEBUG"),
    ] = False,
    non_interactive: Annotated[
        bool,
        typer.Option("--non-interactive", envvar="WEB_SEARCH_CLI_NON_INTERACTIVE"),
    ] = True,
    raw: Annotated[
        bool,
        typer.Option("--raw", help="Emit bare values (one per line) for piping"),
    ] = False,
    fields: Annotated[
        str | None,
        typer.Option("--fields", help="Comma-separated field projection to reduce payload"),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes", "-y", help="Skip confirmation prompt (required in non-interactive mode)"
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Preview feedback create/close/transition without modifying files",
        ),
    ] = False,
) -> None:
    """Native JSON-first CLI for web-search-mcp."""
    effective_log_level: int | str = log_level
    if debug:
        effective_log_level = logging.DEBUG
    configure_logging(level=effective_log_level, log_format=log_format)

    runtime = set_runtime(
        quiet=quiet,
        profile=profile,
        log_level=logging.getLevelName(effective_log_level)
        if isinstance(effective_log_level, int)
        else effective_log_level,
        log_format=log_format,
        debug=debug,
        non_interactive=non_interactive,
        raw=raw,
        fields=fields,
        yes=yes,
        dry_run=dry_run,
    )
    ctx.obj = runtime.as_dict()


feedback.register(app)
skills.register(app)
schema.register(app)
doctor.register(app)
getskill.register(app)
recommend.register(app)
reference.register(app)
search.register(app)
content.register(app)
links.register(app)
inference.register(app)
ai.register(app)
youtube.register(app)
analytics.register(app)
experiments.register(app)
server.register(app)
sitemap.register(app)


def _contains_flag(args: list[str], *flags: str) -> bool:
    return any(flag in args for flag in flags)


def _option_value(args: list[str], flag: str, default: str | None = None) -> str | None:
    prefix = f"{flag}="
    for index, token in enumerate(args):
        if token.startswith(prefix):
            return token.split("=", 1)[1]
        if token == flag and index + 1 < len(args):
            return args[index + 1]
    return default


def _print_special_flags(args: list[str]) -> bool:
    if _contains_flag(args, "--brief"):
        sys.stdout.write(cli_brief() + "\n")
        return True
    if _contains_flag(args, "--version", "-V"):
        sys.stdout.write(cli_version() + "\n")
        return True
    if _contains_flag(args, "--help", "-h"):
        is_quiet = _contains_flag(args, "--quiet", "-q")
        profile = _option_value(args, "--profile", default="full") or "full"
        fields = _option_value(args, "--fields", default=None)
        set_runtime(quiet=is_quiet, profile=profile, fields=fields)
        payload = build_full_help_payload(app, args)
        if is_quiet:
            payload.pop("rules", None)
            payload.pop("skills", None)
            payload.pop("feedback", None)
        emit_json(payload, command=f"{payload['command']} --help")
        return True
    return False


_TRACED_COMMANDS = frozenset({"search", "content", "ai", "youtube"})


def _needs_telemetry(args: list[str]) -> bool:
    """Return True if the command actually needs OpenTelemetry tracing.

    Fast operational commands skip telemetry init to avoid the ~20s Phoenix
    startup delay. Only search, content, AI, and YouTube commands need tracing.
    """
    tokens = command_path_tokens(args)
    return bool(tokens and tokens[0] in _TRACED_COMMANDS)


def main(args: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if args is None else args)
    if _print_special_flags(args):
        return

    try:
        if _needs_telemetry(args):
            from ..telemetry.init import init_telemetry

            init_telemetry(service_name="web-search-mcp")

        app(args=args, prog_name="web-search-cli", standalone_mode=False)
    except CliError as exc:
        emit_error(exc.payload())
        raise SystemExit(int(exc.exit_code)) from exc
    except click.ClickException as exc:
        msg = exc.format_message()
        hint = "Run `web-search-cli schema` to inspect valid commands."
        emit_error(
            {
                "error": {
                    "kind": "usage_error",
                    "code": "usage_error",
                    "message": msg,
                    "hint": hint,
                    "suggestion": hint,
                    "exit_code": int(ExitCode.USAGE_ERROR),
                    "context": {"exception_type": type(exc).__name__},
                }
            }
        )
        raise SystemExit(int(ExitCode.USAGE_ERROR)) from exc
    except Exception as exc:
        msg = str(exc)
        matched = match_hint_rule(msg)
        kind = matched.kind if matched else "internal_error"
        hint = matched.hint if matched else "Run `web-search-cli doctor` to inspect CLI readiness."
        code = matched.exit_code if matched else ExitCode.INTERNAL_ERROR
        emit_error(
            {
                "error": {
                    "kind": kind,
                    "code": kind,
                    "message": msg,
                    "hint": hint,
                    "suggestion": hint,
                    "exit_code": int(code),
                    "context": {"exception_type": type(exc).__name__},
                }
            }
        )
        raise SystemExit(int(code)) from exc
