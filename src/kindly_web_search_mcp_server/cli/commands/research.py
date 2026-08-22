from __future__ import annotations

from typing import Annotated

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..output import emit_json
from ..runtime import run_cli_async
from ..services.files import write_text_atomic


research_app = typer.Typer(no_args_is_help=True)


@research_app.command("deep")
def deep_cmd(
    query: Annotated[str, typer.Option("--query", help="Research topic or question.")],
    depth: Annotated[str, typer.Option("--depth", help="quick, standard, deep, or a supported alias.")] = "standard",
    with_images: Annotated[bool, typer.Option("--with-images/--no-with-images")] = False,
    language_code: Annotated[str | None, typer.Option("--language-code")] = None,
    token_budget_override: Annotated[int | None, typer.Option("--token-budget")] = None,
    team_size_override: Annotated[int | None, typer.Option("--team-size")] = None,
    endpoint_override: Annotated[str | None, typer.Option("--endpoint")] = None,
    output: Annotated[
        str | None,
        typer.Option("--output", help="Atomically write the generated Markdown report to this path."),
    ] = None,
) -> None:
    """Run the existing autonomous deep-research backend."""
    from ..services.deep_research import fetch_deep_research_payload

    if not query.strip():
        raise CliError(
            kind="usage_error",
            message="--query must be a non-blank string.",
            hint="Provide a research topic or question.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "research deep"},
        )

    try:
        payload = run_cli_async(
            fetch_deep_research_payload(
                query,
                depth=depth,
                with_images=with_images,
                language_code=language_code,
                token_budget_override=token_budget_override,
                team_size_override=team_size_override,
                endpoint_override=endpoint_override,
            )
        )
    except ValueError as exc:
        raise CliError(
            kind="usage_error",
            message=str(exc),
            hint="Use depth quick, standard, or deep and valid numeric overrides.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "research deep"},
        ) from exc
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Check DEEP_RESEARCH_URL and retry with --depth quick.",
            exit_code=ExitCode.PROVIDER_ERROR,
            context={"command": "research deep", "exception_type": type(exc).__name__},
        ) from exc

    if output:
        report = payload.get("report_markdown")
        if not isinstance(report, str):
            raise CliError(
                kind="schema_error",
                message="Deep research response did not contain report_markdown.",
                hint="Retry without --output and inspect the structured response.",
                exit_code=ExitCode.SCHEMA_ERROR,
                context={"command": "research deep", "output": output},
            )
        payload["report_path"] = write_text_atomic(output, report)

    emit_json(payload, command="research deep")


@research_app.command("collect")
def collect_cmd(
    query: Annotated[str, typer.Option("--query", help="Search query text.")],
    research_goal: Annotated[str, typer.Option("--research-goal")],
    output_dir: Annotated[
        str, typer.Option("--output-dir", help="Directory for the evidence bundle.")
    ],
    top_results: Annotated[int, typer.Option("--top-results")] = 5,
    rewrite: Annotated[bool, typer.Option("--rewrite/--no-rewrite")] = True,
    ai_summary: Annotated[bool, typer.Option("--ai-summary/--no-ai-summary")] = False,
    no_wait: Annotated[
        bool, typer.Option("--no-wait", help="Submit a local job and return immediately.")
    ] = False,
    idempotency_key: Annotated[str | None, typer.Option("--idempotency-key")] = None,
) -> None:
    """Collect search results and source pages into a deterministic bundle."""
    if not query.strip() or not research_goal.strip() or top_results < 1:
        raise CliError(
            kind="usage_error",
            message="query and research_goal must be non-blank and top_results must be >= 1.",
            hint="Provide valid collection inputs before submitting a job.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "research collect", "output_dir": output_dir},
        )
    if no_wait:
        from ..services.jobs import submit_research_collect_job

        try:
            payload = submit_research_collect_job(
                query,
                research_goal,
                output_dir=output_dir,
                top_results=top_results,
                rewrite=rewrite,
                ai_summary=ai_summary,
                idempotency_key=idempotency_key,
            )
        except ValueError as exc:
            raise CliError(
                kind="usage_error",
                message=str(exc),
                hint="Provide valid collection limits and an output directory.",
                exit_code=ExitCode.USAGE_ERROR,
                context={"command": "research collect", "output_dir": output_dir},
            ) from exc
        emit_json(payload, command="research collect")
        return

    from ..services.research_collect import collect_research_bundle

    try:
        payload = run_cli_async(
            collect_research_bundle(
                query,
                research_goal,
                output_dir=output_dir,
                top_results=top_results,
                rewrite=rewrite,
                ai_summary=ai_summary,
            )
        )
    except ValueError as exc:
        raise CliError(
            kind="usage_error",
            message=str(exc),
            hint="Provide non-blank query/goal values and positive collection limits.",
            exit_code=ExitCode.USAGE_ERROR,
            context={"command": "research collect", "output_dir": output_dir},
        ) from exc
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Run `web-search-cli doctor` and check search/content providers.",
            exit_code=ExitCode.PROVIDER_ERROR,
            context={"command": "research collect", "output_dir": output_dir},
        ) from exc
    emit_json(payload, command="research collect")


def register(app: typer.Typer) -> None:
    app.add_typer(research_app, name="research")
