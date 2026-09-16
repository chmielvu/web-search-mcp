from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from typing import Any

from .services.jobs import finish_job, get_job, is_cancel_requested, mark_running
from .services.research_collect import collect_research_bundle

# Interval between cancellation checks while a job is running.
CANCEL_POLL_SECONDS = 1.0


class JobCancelledError(Exception):
    """Raised when a job stops because cancellation was requested."""


async def _watch_for_cancel(job_id: str, task: asyncio.Task[Any]) -> None:
    """Cancel ``task`` as soon as cancellation is requested for ``job_id``."""
    while not task.done():
        await asyncio.sleep(CANCEL_POLL_SECONDS)
        if await asyncio.to_thread(is_cancel_requested, job_id):
            task.cancel()
            return


async def _collect_with_cancel(job_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Run the collector, interrupting it once cancellation is requested.

    The bundle is assembled through atomic writes and its manifest is written
    last, so unwinding here cannot leave a half-written file behind: an
    interrupted run is a directory without a manifest.
    """
    task = asyncio.create_task(collect_research_bundle(**spec))
    watcher = asyncio.create_task(_watch_for_cancel(job_id, task))
    try:
        return await task
    except asyncio.CancelledError:
        # Only the cancellation this worker requested maps to a cancelled job;
        # any other interruption is not ours to reinterpret.
        if not await asyncio.to_thread(is_cancel_requested, job_id):
            raise
        raise JobCancelledError from None
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher


def run(job_id: str) -> int:
    job = get_job(job_id)
    if not mark_running(job_id, os.getpid()):
        return 0
    if is_cancel_requested(job_id):
        finish_job(job_id, "cancelled", error="Cancellation requested before execution.")
        return 0

    spec = job.get("spec")
    if not isinstance(spec, dict) or job.get("kind") != "research.collect":
        finish_job(job_id, "failed", error="Unsupported job specification.")
        return 1

    try:
        result = asyncio.run(_collect_with_cancel(job_id, spec))
    except JobCancelledError:
        finish_job(job_id, "cancelled", error="Cancellation requested.")
        return 0
    except Exception as exc:
        finish_job(job_id, "failed", error=f"{type(exc).__name__}: {exc}")
        return 1

    if is_cancel_requested(job_id):
        finish_job(job_id, "cancelled", result=result, error="Cancellation requested.")
    else:
        finish_job(job_id, "succeeded", result=result)
    return 0


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("job_worker requires one job id")
    raise SystemExit(run(sys.argv[1]))


if __name__ == "__main__":
    main()
