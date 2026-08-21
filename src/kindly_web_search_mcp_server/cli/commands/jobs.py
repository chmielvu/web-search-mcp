from __future__ import annotations

from typing import Annotated

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..output import emit_json
from ..services.jobs import cancel_job, get_job, list_jobs, resume_job, wait_for_job


jobs_app = typer.Typer(no_args_is_help=True)


def _job_error(command: str, job_id: str, exc: Exception) -> CliError:
    if isinstance(exc, LookupError):
        return CliError(
            kind="not_found",
            message=str(exc),
            hint="Run `web-search-cli jobs list` to inspect available jobs.",
            exit_code=ExitCode.NOT_FOUND,
            context={"command": command, "job_id": job_id},
        )
    return CliError(
        kind="tool_error",
        message=str(exc),
        hint="Check the local CLI job database and retry.",
        exit_code=ExitCode.INTERNAL_ERROR,
        context={"command": command, "job_id": job_id},
    )


@jobs_app.command("list")
def list_cmd(
    limit: Annotated[int, typer.Option("--limit")] = 20,
) -> None:
    jobs = list_jobs(limit)
    emit_json({"jobs": jobs, "total": len(jobs)}, command="jobs list")


@jobs_app.command("get")
def get_cmd(
    job_id: Annotated[str, typer.Argument(help="Job identifier.")],
) -> None:
    """Get one local durable CLI job."""
    try:
        payload = get_job(job_id)
    except Exception as exc:
        raise _job_error("jobs get", job_id, exc) from exc
    emit_json(payload, command="jobs get")


@jobs_app.command("wait")
def wait_cmd(
    job_id: Annotated[str, typer.Argument(help="Job identifier.")],
    timeout_seconds: Annotated[float, typer.Option("--timeout-seconds")] = 300.0,
    poll_interval_seconds: Annotated[float, typer.Option("--poll-interval-seconds")] = 2.0,
) -> None:
    """Wait for a job and return its terminal or partial state."""
    try:
        payload, timed_out = wait_for_job(
            job_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
    except Exception as exc:
        raise _job_error("jobs wait", job_id, exc) from exc
    payload["timed_out"] = timed_out
    emit_json(payload, command="jobs wait")
    if timed_out:
        raise typer.Exit(code=int(ExitCode.TIMEOUT))


@jobs_app.command("cancel")
def cancel_cmd(
    job_id: Annotated[str, typer.Argument(help="Job identifier.")],
) -> None:
    """Request cooperative cancellation of a job."""
    try:
        payload = cancel_job(job_id)
    except Exception as exc:
        raise _job_error("jobs cancel", job_id, exc) from exc
    emit_json(payload, command="jobs cancel")


@jobs_app.command("resume")
def resume_cmd(
    job_id: Annotated[str, typer.Argument(help="Job identifier.")],
) -> None:
    """Resume a failed, cancelled, or partial job."""
    try:
        payload = resume_job(job_id)
    except Exception as exc:
        raise _job_error("jobs resume", job_id, exc) from exc
    emit_json(payload, command="jobs resume")


def register(app: typer.Typer) -> None:
    app.add_typer(jobs_app, name="jobs")
