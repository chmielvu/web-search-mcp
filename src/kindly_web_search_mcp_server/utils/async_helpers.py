"""Async concurrency helpers with early-return deadline support."""

from __future__ import annotations

import asyncio
import logging
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def gather_with_deadline(
    *tasks: asyncio.Task[T],
    deadline_seconds: float,
) -> tuple[list[T], list[BaseException]]:
    """Wait for *tasks* up to *deadline_seconds*; cancel stragglers.

    Returns ``(completed_results, errors)`` where *errors* includes both
    exceptions raised by tasks and ``CancelledError`` from timed-out tasks.

    Fast providers contribute their results immediately — the pipeline
    doesn't wait for the slowest provider in the group.
    """
    if not tasks:
        return [], []

    done, pending = await asyncio.wait(tasks, timeout=deadline_seconds)

    if not pending:
        # All completed within deadline
        return _collect(done)

    # Cancel stragglers
    logger.warning(
        "Cancelling %d tasks that exceeded %.1fs deadline",
        len(pending),
        deadline_seconds,
    )
    for t in pending:
        t.cancel()
    # Wait for cancellation propagation.  Must be long enough for in-flight
    # httpx requests to unwind; too short and the caller closes shared
    # resources (e.g. the httpx client) while tasks still reference them.
    await asyncio.wait(pending, timeout=2.0)

    all_done = done | set(
        t for t in pending if t.done()
    )
    return _collect(all_done)


def _collect(
    tasks: set[asyncio.Task[T]],
) -> tuple[list[T], list[BaseException]]:
    """Extract results and errors from a set of completed tasks."""
    results: list[T] = []
    errors: list[BaseException] = []
    for t in tasks:
        if t.cancelled():
            errors.append(asyncio.CancelledError())
            continue
        try:
            exc = t.exception()
        except asyncio.CancelledError as exc:
            errors.append(exc)
            continue
        if exc is not None:
            errors.append(exc)
        else:
            results.append(t.result())
    return results, errors


def task_completed_successfully(task: asyncio.Task[T]) -> bool:
    """Return True when *task* finished without error or cancellation."""
    if not task.done() or task.cancelled():
        return False
    try:
        return task.exception() is None
    except asyncio.CancelledError:
        return False
