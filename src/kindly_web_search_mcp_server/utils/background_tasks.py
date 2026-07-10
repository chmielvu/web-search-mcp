"""Fire-and-forget background task utilities.

Provides reliable fire-and-forget scheduling for asyncio tasks.
The event loop only keeps weak references to tasks (Python 3.12+),
so tasks that aren't referenced elsewhere may be garbage collected
before they complete. This module keeps strong references to prevent
that.

See: https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_background_tasks: set[asyncio.Task[Any]] = set()


def fire_and_forget(
    coro: Any,
    *,
    name: str | None = None,
) -> asyncio.Task[Any]:
    """Schedule a fire-and-forget background task.

    The task is tracked in a strong-reference set so it is not
    garbage-collected before completion (Python 3.12+ safety).

    Exceptions are logged but never propagated to the caller.
    The task removes itself from the tracking set when done.

    Usage::

        fire_and_forget(
            run_judge_evaluation(run_key=run_key, query=query, ...),
            name="judge-eval",
        )
    """
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    task.add_done_callback(_on_task_done)
    return task


async def drain_background_tasks(
    *,
    name_prefixes: tuple[str, ...] = (),
    timeout_seconds: float = 5.0,
) -> None:
    """Wait briefly for selected background tasks without cancelling them."""
    if not _background_tasks:
        return
    tasks = [
        task
        for task in list(_background_tasks)
        if not task.done()
        and (
            not name_prefixes or any(task.get_name().startswith(prefix) for prefix in name_prefixes)
        )
    ]
    if not tasks:
        return
    done, _pending = await asyncio.wait(tasks, timeout=max(0.0, timeout_seconds))
    for task in done:
        _background_tasks.discard(task)


def _on_task_done(task: asyncio.Task[Any]) -> None:
    """Remove completed task from tracking set and log any exception."""
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        name = task.get_name() if hasattr(task, "get_name") else "unknown"
        logger.debug("Background task %s failed: %s", name, exc)


async def cancel_all_background_tasks() -> None:
    """Cancel all tracked background tasks and wait for them to finish.

    Called during server shutdown to clean up gracefully.
    """
    if not _background_tasks:
        return
    for task in _background_tasks:
        task.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    _background_tasks.clear()


def background_task_count() -> int:
    """Return the number of currently tracked background tasks."""
    return len(_background_tasks)
