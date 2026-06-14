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
