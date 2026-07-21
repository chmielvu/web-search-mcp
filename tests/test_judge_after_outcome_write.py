"""Regression test: judge must not run before the search_outcome write completes.

The original wiring fired `schedule_judge_search_run` immediately after
`dispatch_duckdb_write`, but `dispatch_duckdb_write` is asynchronous — the
search_runs row did not exist yet when the judge opened its own connection,
so production would write zero judgments on every real search.

This test pins the fix at runtime: a controllable `Future` on the write side,
and an inspectable scheduler on the judge side. The judge must NOT be called
until the writer's future is set to finished (success or failure).
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import Future
from typing import Any
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_search_run() -> MagicMock:
    """Minimal SearchRun stand-in for persist_search_outcome."""
    run = MagicMock()
    run.run_key = "test-race-1"
    outcome = MagicMock()
    outcome.run_key = "test-race-1"
    outcome.tool_call_id = None
    outcome.session_id = None
    outcome.error_summary = None
    outcome.rerank_metadata = {"funnel_counts": {}}
    outcome.timings = {}
    outcome.plan = None
    outcome.request.query = "q"
    outcome.request.research_goal = "g"
    outcome.request.num_results = 10
    outcome.request.rewrite = True
    outcome.response = MagicMock()
    outcome.response.results = ()
    outcome.outcomes = ()
    run.snapshot.return_value = outcome
    run.diagnostics.total_latency_ms = 0.0
    run.diagnostics.enrichment = {}
    run.diagnostics.rewrite_metadata = {"branch_count": 6}
    run.diagnostics.merge_counts = {}
    run.diagnostics.rerank_stage_summaries = []
    run.diagnostics.phase_timings = {}
    return run


@pytest.fixture
def patched_dispatch_and_judge(monkeypatch):
    """Patch dispatch_duckdb_write + schedule_judge_search_run.

    `persist_search_outcome()` does
        `from ..analytics.async_writes import dispatch_duckdb_write`
    inside the function, so we patch the symbol on `async_writes` (the
    source module), not on the `outcomes` namespace. Returns:
      - `pending`: a Future the test can complete manually
      - `schedule_calls`: list of run_keys passed to schedule_judge_search_run
      - `captured_writer`: the writer closure passed to dispatch (test can
         invoke it directly to exercise `_write()`'s success/failure paths)
    """
    pending: Future[None] = Future()
    schedule_calls: list[str] = []
    captured_writer: dict[str, Any] = {}

    from kindly_web_search_mcp_server.analytics import async_writes as aw
    from kindly_web_search_mcp_server.analytics import judges as jm

    def fake_dispatch(task_name: str, writer):
        captured_writer["fn"] = writer
        return pending

    monkeypatch.setattr(aw, "dispatch_duckdb_write", fake_dispatch)
    monkeypatch.setattr(jm, "schedule_judge_search_run", lambda rk: schedule_calls.append(rk))

    return {
        "pending": pending,
        "schedule_calls": schedule_calls,
        "captured_writer": captured_writer,
    }


def _wait_for(predicate, *, timeout: float = 5.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_judge_not_scheduled_until_write_future_completes(
    patched_dispatch_and_judge, fake_search_run
):
    """While the write future is pending, schedule_judge_search_run must not fire."""
    from kindly_web_search_mcp_server.search import outcomes as outcomes_module

    pending: Future[None] = patched_dispatch_and_judge["pending"]
    schedule_calls: list[str] = patched_dispatch_and_judge["schedule_calls"]

    asyncio.run(outcomes_module.persist_search_outcome(fake_search_run))

    assert pending.done() is False, "future should still be pending"
    assert schedule_calls == [], (
        f"judge was scheduled while write future was pending: {schedule_calls}"
    )

    pending.set_result(None)

    ok = _wait_for(lambda: schedule_calls == ["test-race-1"], timeout=5.0)
    assert ok, f"judge not scheduled after write future completed: {schedule_calls}"


def test_judge_not_scheduled_when_write_future_raises(patched_dispatch_and_judge, fake_search_run):
    """If the write future raises (after `_write()` returns), the judge must NOT be scheduled.

    This guards the `future.result()` check inside the done-callback.
    """
    from kindly_web_search_mcp_server.search import outcomes as outcomes_module

    pending: Future[None] = patched_dispatch_and_judge["pending"]
    schedule_calls: list[str] = patched_dispatch_and_judge["schedule_calls"]

    asyncio.run(outcomes_module.persist_search_outcome(fake_search_run))

    pending.set_exception(RuntimeError("write failed"))

    time.sleep(0.3)
    assert schedule_calls == [], f"judge scheduled despite write future raising: {schedule_calls}"


@pytest.fixture
def _patch_failing_primary_search_run(monkeypatch):
    """Replace analytics.duckdb_store.insert_search_run with a raising stub.

    `persist_search_outcome()` captures this exact symbol in
    writes[0]['_w'], so patching it here propagates to the captured
    writer and exercises the new failure-propagation path in `_write()`.
    """

    def failing_search_run(**_kwargs):
        raise RuntimeError("insert_search_run failed: simulated")

    import kindly_web_search_mcp_server.analytics.duckdb_store as ds

    monkeypatch.setattr(ds, "insert_search_run", failing_search_run)
    return failing_search_run


def test_judge_not_scheduled_when_primary_insert_search_run_fails(
    patched_dispatch_and_judge, fake_search_run, _patch_failing_primary_search_run
):
    """If insert_search_run (the PRIMARY writer) raises, judge must NOT be scheduled.

    Previously `_write()` swallowed every per-row exception, so the
    done-callback saw a successful Future even when `search_runs` was
    never inserted. The fix: let the primary's exception propagate.
    """
    from kindly_web_search_mcp_server.search import outcomes as outcomes_module

    pending: Future[None] = patched_dispatch_and_judge["pending"]
    schedule_calls: list[str] = patched_dispatch_and_judge["schedule_calls"]
    captured_writer: dict[str, Any] = patched_dispatch_and_judge["captured_writer"]

    asyncio.run(outcomes_module.persist_search_outcome(fake_search_run))

    # Invoke the captured writer exactly once. `pytest.raises` captures
    # the raised exception so we can propagate the SAME instance to
    # the Future without a second writer call. This proves the primary
    # failure escapes `_write()` rather than being swallowed, and that
    # the done-callback sees a failed Future and skips scheduling.
    with pytest.raises(RuntimeError, match="insert_search_run failed") as exc_info:
        captured_writer["fn"]()

    pending.set_exception(exc_info.value)

    time.sleep(0.3)
    assert schedule_calls == [], (
        f"judge scheduled despite primary insert_search_run failing: {schedule_calls}"
    )


def test_dispatch_duckdb_write_returns_future_on_async_path(monkeypatch):
    """dispatch_duckdb_write must return a Future on the async path."""
    from kindly_web_search_mcp_server.analytics import async_writes

    class FakeExecutor:
        def submit(self, _fn):
            f: Future[None] = Future()
            f.set_result(None)
            return f

    monkeypatch.setattr(async_writes, "_get_duckdb_write_executor", lambda: FakeExecutor())

    async def driver() -> Any:
        return async_writes.dispatch_duckdb_write("test", lambda: None)

    result = asyncio.run(driver())
    assert isinstance(result, Future), f"expected Future, got {type(result)}"


def test_dispatch_duckdb_write_returns_future_on_sync_path():
    """dispatch_duckdb_write must return a Future even with no event loop."""
    from kindly_web_search_mcp_server.analytics import async_writes

    result = async_writes.dispatch_duckdb_write("test-sync", lambda: None)
    assert isinstance(result, Future), f"expected Future, got {type(result)}"
    assert result.done() is True, "sync writer ran inline; future should be done"
