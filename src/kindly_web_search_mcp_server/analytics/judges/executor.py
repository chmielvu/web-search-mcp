"""Daemon thread-pool executor + lifecycle for fire-and-forget judge runs.

Owns the shared judge ThreadPoolExecutor and the cross-thread schedule lock
that serialises lifecycle checks with executor acquisition and shutdown.
``shutdown_judge_executor`` cancels pending facet/run jobs and advances the
executor generation so a future ``_get_judge_executor`` call rebuilds lazily.
``drain_judges`` blocks until every pending ``schedule_judge_search_run``
future resolves, intended for graceful CLI shutdown.
"""

from __future__ import annotations

import logging
import threading
import weakref
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures.thread import _worker  # type: ignore[attr-defined]
from dataclasses import dataclass
from threading import Lock

logger = logging.getLogger(__name__)


class _DaemonThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor with daemon workers that atexit will not join.

    Mirrors CPython's ``_adjust_thread_count`` but:
    - sets ``daemon=True`` before ``start()``
    - does **not** insert into ``_threads_queues`` (so ``_python_exit``
      cannot block process exit on abandoned judge work)
    """

    def _adjust_thread_count(self) -> None:
        if self._idle_semaphore.acquire(timeout=0):
            return

        def weakref_cb(_, q=self._work_queue):
            q.put(None)  # pyright: ignore[reportArgumentType]

        num_threads = len(self._threads)
        if num_threads < self._max_workers:
            thread_name = f"{self._thread_name_prefix or self}_{num_threads}"
            t = threading.Thread(
                name=thread_name,
                target=_worker,
                args=(
                    weakref.ref(self, weakref_cb),
                    self._work_queue,
                    self._initializer,
                    self._initargs,
                ),
                daemon=True,
            )
            t.start()
            self._threads.add(t)  # pyright: ignore[reportAttributeAccessIssue]
            # Deliberately skip: _threads_queues[t] = self._work_queue


@dataclass(frozen=True, slots=True)
class _JudgeExecutorLifecycle:
    generation: int
    state: str


_JUDGE_EXECUTOR: ThreadPoolExecutor | None = None
_JUDGE_EXECUTOR_LOCK = Lock()
_JUDGE_LIFECYCLE = _JudgeExecutorLifecycle(generation=0, state="running")
_JUDGE_SCHEDULE_LOCK = Lock()
"""Serializes lifecycle checks with executor acquisition and shutdown."""


def _get_judge_executor() -> ThreadPoolExecutor:
    global _JUDGE_EXECUTOR
    if _JUDGE_EXECUTOR is None:
        with _JUDGE_EXECUTOR_LOCK:
            if _JUDGE_EXECUTOR is None:
                _JUDGE_EXECUTOR = _DaemonThreadPoolExecutor(
                    max_workers=4,
                    thread_name_prefix="judge",
                )
    return _JUDGE_EXECUTOR


def shutdown_judge_executor(*, wait: bool = False) -> None:
    """Stop the shared judge ThreadPoolExecutor.

    ``wait=False`` cancels pending facet/run jobs (``cancel_futures=True``).
    Workers are daemon and not in concurrent.futures' atexit join map, so CLI
    process exit is not pinned by in-flight judge calls.

    The lifecycle state blocks new submissions only while this shutdown is in
    progress. Completion advances the executor generation and reopens the
    scheduler for a fresh lazy executor.
    """
    global _JUDGE_EXECUTOR, _JUDGE_LIFECYCLE
    with _JUDGE_SCHEDULE_LOCK:
        if _JUDGE_LIFECYCLE.state == "shutting_down":
            return
        generation = _JUDGE_LIFECYCLE.generation
        _JUDGE_LIFECYCLE = _JudgeExecutorLifecycle(
            generation=generation,
            state="shutting_down",
        )
        with _JUDGE_EXECUTOR_LOCK:
            executor = _JUDGE_EXECUTOR
            _JUDGE_EXECUTOR = None
    try:
        if executor is not None:
            executor.shutdown(wait=wait, cancel_futures=not wait)
    finally:
        with _JUDGE_SCHEDULE_LOCK:
            if (
                _JUDGE_LIFECYCLE.generation == generation
                and _JUDGE_LIFECYCLE.state == "shutting_down"
            ):
                _JUDGE_LIFECYCLE = _JudgeExecutorLifecycle(
                    generation=generation + 1,
                    state="running",
                )


_PENDING_JUDGE_FUTURES: set[Future[int]] = set()
_PENDING_JUDGE_FUTURES_LOCK = Lock()


def drain_judges(timeout_seconds: float = 30.0) -> None:
    """Wait for all pending judge futures to complete before shutdown."""
    from concurrent.futures import wait

    with _PENDING_JUDGE_FUTURES_LOCK:
        futures = list(_PENDING_JUDGE_FUTURES)
    if not futures:
        return
    _done, not_done = wait(futures, timeout=timeout_seconds)
    if not_done:
        logger.warning("%d judge tasks timed out during drain", len(not_done))
