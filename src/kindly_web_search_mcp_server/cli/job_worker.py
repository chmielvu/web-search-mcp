from __future__ import annotations

import asyncio
import os
import sys

from .services.jobs import finish_job, get_job, is_cancel_requested, mark_running
from .services.research_collect import collect_research_bundle


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
        result = asyncio.run(collect_research_bundle(**spec))
        if is_cancel_requested(job_id):
            finish_job(job_id, "cancelled", result=result, error="Cancellation requested.")
        else:
            finish_job(job_id, "succeeded", result=result)
        return 0
    except Exception as exc:  # pragma: no cover - exercised through subprocess tests
        finish_job(job_id, "failed", error=f"{type(exc).__name__}: {exc}")
        return 1


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("job_worker requires one job id")
    raise SystemExit(run(sys.argv[1]))


if __name__ == "__main__":
    main()
