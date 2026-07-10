from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Coroutine, Literal


OutputMode = Literal["agent", "human"]


@dataclass(slots=True)
class CliRuntime:
    output_mode: OutputMode = "agent"
    profile: str = "full"
    quiet: bool = False
    log_level: str = "error"
    non_interactive: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "output_mode": self.output_mode,
            "profile": self.profile,
            "quiet": self.quiet,
            "log_level": self.log_level,
            "non_interactive": self.non_interactive,
        }


_RUNTIME = CliRuntime()


def set_runtime(
    *,
    agent: bool = True,
    human: bool = False,
    quiet: bool = False,
    profile: str = "full",
    log_level: str = "error",
    non_interactive: bool = True,
) -> CliRuntime:
    runtime = CliRuntime(
        output_mode="human" if human else "agent",
        profile=profile,
        quiet=quiet,
        log_level=log_level,
        non_interactive=non_interactive,
    )
    global _RUNTIME
    _RUNTIME = runtime
    return runtime


def get_runtime() -> CliRuntime:
    return _RUNTIME


def run_cli_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a CLI command coroutine to completion, then drain background tasks and shut down the write executor."""
    # Local shutdown dependencies remain lazy to avoid import-time side effects.

    async def _runner() -> Any:
        try:
            return await coro
        finally:
            from ..utils.background_tasks import drain_background_tasks
            from ..analytics.async_writes import shutdown_duckdb_write_executor
            from ..settings import settings

            try:
                await drain_background_tasks(
                    name_prefixes=("analytics.",),
                    timeout_seconds=settings.analytics_shutdown_drain_timeout_seconds,
                )
            except Exception:
                pass
            try:
                shutdown_duckdb_write_executor(wait=True)
            except Exception:
                pass

    return asyncio.run(_runner())
