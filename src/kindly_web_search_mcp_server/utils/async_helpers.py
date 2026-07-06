"""Async concurrency helpers with deadline-aware cancellation draining."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Cancelled tasks are given this bounded window to finish their cleanup
# (release HTTP connections, close file handles, etc.).  A stuck provider whose
# transport ignores cancellation must not be allowed to stall the whole pipeline
# forever, which is what happens when the drain is unbounded.  The value is a
# trade-off: too short means legitimate cleanup cannot finish; too long means
# a single bad provider inflates tail latency.  Three seconds covers the common
# httpx connection-teardown path while remaining acceptable for interactive
# tool calls.
DEFAULT_DRAIN_TIMEOUT_SECONDS: float = 3.0


def _ignore_task_exception(task: asyncio.Task[Any]) -> None:
    """Retrieve a task's exception so asyncio does not log unretrieved warnings."""
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass


async def gather_with_deadline(
    *tasks: asyncio.Task[T],
    deadline_seconds: float,
    drain_timeout_seconds: float = DEFAULT_DRAIN_TIMEOUT_SECONDS,
) -> tuple[list[T], list[BaseException]]:
    """Wait for *tasks* up to *deadline_seconds*; cancel and drain stragglers.

    Returns ``(completed_results, errors)`` where *errors* includes both
    exceptions raised by tasks and ``CancelledError`` from timed-out or
    abandoned tasks.

    Cancelled tasks are given *drain_timeout_seconds* to finish cleanup (e.g.
    release HTTP connections).  Tasks that have not completed after that
    bounded grace window are **abandoned**: the helper returns, but a done
    callback is attached to each abandoned task so that asyncio does not log
    "unretrieved exception" warnings when the task finally finishes.

    This bounded drain is essential when tasks hold shared resources such as
    a long-lived ``httpx.AsyncClient``: it lets most cleanup complete normally
    while guaranteeing that a single stuck task cannot hang the entire caller.
    """
    if not tasks:
        return [], []

    done, pending = await asyncio.wait(tasks, timeout=deadline_seconds)

    if not pending:
        return _collect(done)

    logger.warning(
        "Cancelling %d tasks that exceeded %.1fs deadline",
        len(pending),
        deadline_seconds,
    )
    for t in pending:
        t.cancel()

    # Bounded drain: give cancelled tasks a window to unwind, then stop waiting.
    drain_done: set[asyncio.Task[T]] = set()
    drain_pending: set[asyncio.Task[T]] = pending
    if pending:
        drain_done, drain_pending = await asyncio.wait(pending, timeout=drain_timeout_seconds)

    if drain_pending:
        logger.warning(
            "%d tasks did not finish cleanup within %.1fs drain window; abandoning",
            len(drain_pending),
            drain_timeout_seconds,
        )
        for t in drain_pending:
            # Retrieve the exception asynchronously so asyncio doesn't warn.
            t.add_done_callback(_ignore_task_exception)

    return _collect(done | drain_done, abandoned=drain_pending)


def _collect(
    tasks: set[asyncio.Task[T]],
    *,
    abandoned: set[asyncio.Task[T]] | None = None,
) -> tuple[list[T], list[BaseException]]:
    """Extract results and errors from a set of completed tasks.

    Completed tasks contribute their result or exception.  Abandoned tasks
    (those still pending after the bounded drain) are treated as cancelled.
    """
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

    for _ in abandoned or set():
        errors.append(asyncio.CancelledError("abandoned after drain timeout"))

    return results, errors


def task_completed_successfully(task: asyncio.Task[T]) -> bool:
    """Return True when *task* finished without error or cancellation."""
    if not task.done() or task.cancelled():
        return False
    try:
        return task.exception() is None
    except asyncio.CancelledError:
        return False
