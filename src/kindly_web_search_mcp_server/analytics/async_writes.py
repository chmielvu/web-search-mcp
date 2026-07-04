from __future__ import annotations

import asyncio
from collections.abc import Callable

from ..utils.background_tasks import fire_and_forget


def dispatch_duckdb_write(task_name: str, writer: Callable[[], None]) -> None:
    """Run a blocking DuckDB write off the event loop when possible."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        writer()
        return

    fire_and_forget(asyncio.to_thread(writer), name=task_name)
