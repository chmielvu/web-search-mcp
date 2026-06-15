from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_gather_with_deadline_returns_fast_results_and_cancel_errors() -> None:
    from kindly_web_search_mcp_server.utils.async_helpers import gather_with_deadline

    async def _fast() -> str:
        return "fast"

    async def _slow() -> str:
        await asyncio.sleep(0.2)
        return "slow"

    async def _run() -> None:
        fast_task = asyncio.create_task(_fast())
        slow_task = asyncio.create_task(_slow())

        results, errors = await gather_with_deadline(
            fast_task,
            slow_task,
            deadline_seconds=0.01,
        )

        assert results == ["fast"]
        assert any(isinstance(error, asyncio.CancelledError) for error in errors)
        assert fast_task.done()
        assert slow_task.cancelled()

    asyncio.run(_run())


def test_gather_with_deadline_waits_for_cancelled_cleanup_to_finish() -> None:
    from kindly_web_search_mcp_server.utils.async_helpers import gather_with_deadline

    async def _fast() -> str:
        return "fast"

    async def _slow() -> str:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(0.2)
            return "slow"

    async def _run() -> None:
        fast_task = asyncio.create_task(_fast())
        slow_task = asyncio.create_task(_slow())

        results, errors = await gather_with_deadline(
            fast_task,
            slow_task,
            deadline_seconds=0.01,
            drain_timeout_seconds=0.5,
        )

        assert set(results) == {"fast", "slow"}
        assert errors == []
        assert fast_task.done()
        assert slow_task.done()
        assert slow_task.result() == "slow"

    asyncio.run(_run())


def test_gather_with_deadline_abandons_tasks_that_ignore_cancellation() -> None:
    from kindly_web_search_mcp_server.utils.async_helpers import gather_with_deadline

    async def _slow_but_completes() -> str:
        return "completed"

    async def _ignores_cancellation_until_released(stop: asyncio.Event) -> str:
        try:
            await stop.wait()
        except asyncio.CancelledError:
            # Simulate a provider that swallows cancellation and waits for its
            # own cleanup condition (e.g. a stuck transport read).
            await stop.wait()
        return "never"

    async def _run() -> None:
        stop = asyncio.Event()
        completed_task = asyncio.create_task(_slow_but_completes())
        stuck_task = asyncio.create_task(_ignores_cancellation_until_released(stop))

        start = asyncio.get_event_loop().time()
        results, errors = await gather_with_deadline(
            completed_task,
            stuck_task,
            deadline_seconds=0.01,
            drain_timeout_seconds=0.05,
        )
        elapsed = asyncio.get_event_loop().time() - start

        # The helper must return soon after deadline + drain_timeout, not wait
        # for the stuck cleanup routine.
        assert elapsed <= 0.5, f"helper took {elapsed:.2f}s, expected <= 0.5s"
        assert results == ["completed"]
        assert len(errors) == 1
        assert isinstance(errors[0], asyncio.CancelledError)
        assert errors[0].args[0] == "abandoned after drain timeout"
        assert completed_task.done()

        # Release the abandoned task so asyncio.run can shut down cleanly.
        stop.set()

    asyncio.run(_run())
