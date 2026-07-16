"""Structured concurrency scope with hard deadline enforcement.

Each coroutine created via ``TaskScope.create_task`` is independently wrapped
with ``asyncio.wait_for(timeout=deadline)`` before becoming a task. This
ensures that the ``CancelledError`` is delivered to each task individually
at the deadline boundary, rather than relying on a batch-level timeout that
fails to stop tasks that ignore cooperative cancellation.

Provides a ``threading.Event`` cancellation token for thread-pool workers
that cannot receive ``CancelledError``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Iterable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_DRAIN_SECONDS: float = 3.0


def _ignore_task_exception(task: asyncio.Task[Any]) -> None:
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass


_DRAINING_TASKS: set[asyncio.Task[Any]] = set()


def _release_draining_task(task: asyncio.Task[Any]) -> None:
    _DRAINING_TASKS.discard(task)
    _ignore_task_exception(task)


async def cancel_and_drain_tasks(
    tasks: Iterable[asyncio.Task[Any]],
    *,
    drain_seconds: float = DEFAULT_DRAIN_SECONDS,
) -> set[asyncio.Task[Any]]:
    pending = {t for t in tasks if not t.done()}
    if not pending:
        return set()

    for t in pending:
        t.cancel()

    _, drain_pending = await asyncio.wait(pending, timeout=max(0.1, drain_seconds))

    for t in drain_pending:
        _DRAINING_TASKS.add(t)
        t.add_done_callback(_release_draining_task)

    return drain_pending


class CancellationToken:
    """Cross-thread cancellation signal for thread-pool workers."""

    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()


class TaskScope:
    """Run coroutines concurrently with a hard, per-task deadline.

    Every coroutine registered with ``create_task`` is individually guarded
    by ``asyncio.wait_for(deadline)``. When the scope is exited, any
    surviving task is cancelled and given a bounded drain window for cleanup.

    Usage::

        scope = TaskScope(deadline_seconds=15.0)
        t1 = scope.create_task(fetch_provider("a"))
        t2 = scope.create_task(fetch_provider("b"))
        done, pending = await scope.wait_and_cancel()

        for t in done:
            try:
                results.append(t.result())
            except Exception:
                pass

    Short-form with context manager::

        async with TaskScope(deadline_seconds=15.0) as scope:
            scope.create_task(fetch("a"))
            scope.create_task(fetch("b"))

        results = scope.collect_results()
    """

    def __init__(
        self,
        deadline_seconds: float,
        *,
        drain_seconds: float = DEFAULT_DRAIN_SECONDS,
    ) -> None:
        if deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be > 0")
        self._deadline = deadline_seconds
        self._drain_seconds = max(0.1, drain_seconds)
        self._tasks: list[asyncio.Task[Any]] = []
        self._start_time: float = 0.0
        self._cancel_token = CancellationToken()

    @property
    def cancel_token(self) -> CancellationToken:
        """Return a token that thread-pool workers can poll for early exit."""
        return self._cancel_token

    @property
    def tasks(self) -> list[asyncio.Task[Any]]:
        return list(self._tasks)

    def create_task(self, coro: Any) -> asyncio.Task[Any]:
        """Register *coro* guarded by a per-task deadline."""
        deadline_coro = asyncio.wait_for(coro, timeout=self._deadline)
        task = asyncio.create_task(deadline_coro)
        self._tasks.append(task)
        return task

    async def wait_and_cancel(
        self,
    ) -> tuple[set[asyncio.Task[Any]], set[asyncio.Task[Any]]]:
        """Wait up to deadline + buffer, cancel stragglers with bounded drain.

        Returns (done_tasks, abandoned_tasks).
        """
        elapsed = time.monotonic() - self._start_time if self._start_time else 0.0
        remaining = max(0.1, self._deadline - elapsed + self._drain_seconds / 2)

        done, pending = await asyncio.wait(
            self._tasks, timeout=remaining, return_when=asyncio.ALL_COMPLETED
        )

        if not pending:
            return done, pending

        logger.warning(
            "%d tasks exceeded %.1fs deadline, cancelling",
            len(pending),
            self._deadline,
        )
        self._cancel_token.cancel()

        drain_pending = await cancel_and_drain_tasks(pending, drain_seconds=self._drain_seconds)
        drain_done = pending - drain_pending

        if drain_pending:
            logger.warning(
                "%d tasks did not cancel within %.1fs drain; abandoning",
                len(drain_pending),
                self._drain_seconds,
            )

        return done | drain_done, drain_pending

    def collect_results(
        self,
        *,
        default: T | None = None,
    ) -> list[T]:
        """Return results from completed tasks, ignoring cancelled/failed ones.

        *default* is used for tasks that didn't produce a valid result.
        """
        results: list[T] = []
        for t in self._tasks:
            if not t.done() or t.cancelled():
                if default is not None:
                    results.append(default)
                continue
            try:
                exc = t.exception()
            except asyncio.CancelledError:
                if default is not None:
                    results.append(default)
                continue
            if exc is not None:
                if default is not None:
                    results.append(default)
                continue
            results.append(t.result())  # type: ignore[arg-type]
        return results

    def completed(self, task: asyncio.Task[Any]) -> bool:
        """Return True if *task* finished successfully."""
        if not task.done() or task.cancelled():
            return False
        try:
            return task.exception() is None
        except asyncio.CancelledError:
            return False

    async def __aenter__(self) -> TaskScope:
        self._start_time = time.monotonic()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.wait_and_cancel()


def task_completed_successfully(task: asyncio.Task[T]) -> bool:
    """Return True when *task* finished without error or cancellation."""
    if not task.done() or task.cancelled():
        return False
    try:
        return task.exception() is None
    except asyncio.CancelledError:
        return False
