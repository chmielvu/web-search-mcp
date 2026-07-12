from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
import logging
from threading import Lock

from ..utils.background_tasks import fire_and_forget

LOGGER = logging.getLogger(__name__)
_DUCKDB_WRITE_EXECUTOR_LOCK = Lock()
_DUCKDB_WRITE_EXECUTOR: ThreadPoolExecutor | None = None
_DUCKDB_WRITE_FUTURES: set[Future[None]] = set()
_DUCKDB_WRITE_FUTURES_LOCK = Lock()


def _get_duckdb_write_executor() -> ThreadPoolExecutor:
    global _DUCKDB_WRITE_EXECUTOR
    if _DUCKDB_WRITE_EXECUTOR is None:
        with _DUCKDB_WRITE_EXECUTOR_LOCK:
            if _DUCKDB_WRITE_EXECUTOR is None:
                _DUCKDB_WRITE_EXECUTOR = ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix="duckdb-writes",
                )
    return _DUCKDB_WRITE_EXECUTOR


def shutdown_duckdb_write_executor(*, wait: bool = False) -> None:
    """Stop the dedicated DuckDB write executor."""

    global _DUCKDB_WRITE_EXECUTOR
    with _DUCKDB_WRITE_EXECUTOR_LOCK:
        executor = _DUCKDB_WRITE_EXECUTOR
        _DUCKDB_WRITE_EXECUTOR = None
    if executor is not None:
        executor.shutdown(wait=wait, cancel_futures=not wait)


def drain_duckdb_writes(*, timeout: float = 10.0) -> None:
    """Wait for pending DuckDB write futures to complete.

    Synchronous — uses ``concurrent.futures.wait`` so it works even as
    the event loop winds down.  Call from a worker thread (e.g. via
    ``asyncio.to_thread``) when invoked from an async context.
    """
    with _DUCKDB_WRITE_FUTURES_LOCK:
        pending = set(_DUCKDB_WRITE_FUTURES)
    if not pending:
        return
    wait(pending, timeout=timeout)


def _track_write_future(future: Future[None]) -> None:
    with _DUCKDB_WRITE_FUTURES_LOCK:
        _DUCKDB_WRITE_FUTURES.add(future)

    def _done(completed: Future[None]) -> None:
        with _DUCKDB_WRITE_FUTURES_LOCK:
            _DUCKDB_WRITE_FUTURES.discard(completed)
        if completed.cancelled():
            return
        try:
            completed.result()
        except Exception as exc:
            LOGGER.debug("DuckDB analytics write failed: %s: %s", type(exc).__name__, exc)

    future.add_done_callback(_done)


def dispatch_duckdb_write(task_name: str, writer: Callable[[], None]) -> None:
    """Run a blocking DuckDB write off the event loop when possible."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        writer()
        return

    future = _get_duckdb_write_executor().submit(writer)
    _track_write_future(future)

    async def _run_writer() -> None:
        await asyncio.wrap_future(future)

    fire_and_forget(_run_writer(), name=task_name)
