from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Coroutine


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class CliRuntime:
    profile: str = "full"
    quiet: bool = False
    log_level: str = "error"
    log_format: str = "text"
    debug: bool = False
    non_interactive: bool = True
    raw: bool = False
    fields: str | None = None
    yes: bool = False
    dry_run: bool = False
    last_duration_ms: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "quiet": self.quiet,
            "log_level": self.log_level,
            "log_format": self.log_format,
            "debug": self.debug,
            "non_interactive": self.non_interactive,
            "raw": self.raw,
            "fields": self.fields,
            "yes": self.yes,
            "dry_run": self.dry_run,
        }


_RUNTIME = CliRuntime()


def set_runtime(
    *,
    quiet: bool = False,
    profile: str = "full",
    log_level: str = "error",
    log_format: str = "text",
    debug: bool = False,
    non_interactive: bool = True,
    raw: bool = False,
    fields: str | None = None,
    yes: bool = False,
    dry_run: bool = False,
) -> CliRuntime:
    runtime = CliRuntime(
        profile=profile,
        quiet=quiet,
        log_level=log_level,
        log_format=log_format,
        debug=debug,
        non_interactive=non_interactive,
        raw=raw,
        fields=fields,
        yes=yes,
        dry_run=dry_run,
    )
    global _RUNTIME
    _RUNTIME = runtime
    return runtime


def get_runtime() -> CliRuntime:
    return _RUNTIME


def run_cli_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a CLI command coroutine to completion, then drain background tasks and shut down the write executor."""
    # Local shutdown dependencies remain lazy to avoid import-time side effects.
    runner_finished: float | None = None

    async def _runner() -> Any:
        try:
            return await coro
        finally:
            from ..utils.background_tasks import drain_background_tasks
            from ..analytics.async_writes import (
                drain_duckdb_writes,
                shutdown_duckdb_write_executor,
            )
            from ..search.outcomes import drain_search_outcomes
            from ..settings import settings
            from ..telemetry.init import shutdown_telemetry
            from ..content.remote_clients import close_crawl4ai_client, close_camoufox_client
            from ..utils.http_client import close_http_client

            shutdown_started = time.perf_counter()
            timings: dict[str, float] = {}

            step_started = time.perf_counter()
            try:
                await drain_search_outcomes(settings.analytics_shutdown_drain_timeout_seconds)
            except Exception as exc:
                LOGGER.warning("Failed to drain search outcomes: %s", exc)
            timings["search_outcomes"] = time.perf_counter() - step_started

            step_started = time.perf_counter()
            try:
                await drain_background_tasks(
                    name_prefixes=("analytics.",),
                    timeout_seconds=settings.analytics_shutdown_drain_timeout_seconds,
                )
            except Exception as exc:
                LOGGER.warning("Failed to drain analytics background tasks: %s", exc)
            timings["background_tasks"] = time.perf_counter() - step_started

            step_started = time.perf_counter()
            try:
                await asyncio.to_thread(
                    drain_duckdb_writes,
                    timeout=settings.analytics_shutdown_drain_timeout_seconds,
                )
            except Exception as exc:
                LOGGER.warning("Failed to drain DuckDB writes: %s", exc)
            timings["duckdb_drain"] = time.perf_counter() - step_started

            step_started = time.perf_counter()
            try:
                # Final CLI JSON must not be emitted while a DuckDB writer
                # still owns the analytics file. A bounded drain handles the
                # normal case; wait=True then closes any writer that was
                # already running instead of leaving a live lock behind.
                shutdown_duckdb_write_executor(wait=True)
            except Exception as exc:
                LOGGER.warning("Failed to shut down DuckDB write executor: %s", exc)
            timings["duckdb_executor"] = time.perf_counter() - step_started

            step_started = time.perf_counter()
            try:
                from ..analytics.judges import drain_judges, shutdown_judge_executor

                await asyncio.to_thread(
                    drain_judges,
                    timeout_seconds=settings.analytics_shutdown_drain_timeout_seconds,
                )
                shutdown_judge_executor(wait=False)
            except Exception as exc:
                LOGGER.warning("Failed to shut down judge executor: %s", exc)
            timings["judge_executor"] = time.perf_counter() - step_started

            step_started = time.perf_counter()
            try:
                await close_http_client()
            except Exception as exc:
                LOGGER.warning("Failed to close shared HTTP client: %s", exc)
            timings["http_client"] = time.perf_counter() - step_started

            step_started = time.perf_counter()
            try:
                await close_crawl4ai_client()
                await close_camoufox_client()
            except Exception as exc:
                LOGGER.warning("Failed to close remote content clients: %s", exc)
            timings["remote_clients"] = time.perf_counter() - step_started

            step_started = time.perf_counter()
            try:
                shutdown_telemetry()
            except Exception as exc:
                LOGGER.warning("Failed to shut down telemetry: %s", exc)
            timings["telemetry"] = time.perf_counter() - step_started

            LOGGER.info(
                "CLI shutdown finished in %.3fs "
                "(search_outcomes=%.3fs background_tasks=%.3fs "
                "duckdb_drain=%.3fs duckdb_executor=%.3fs judge_executor=%.3fs "
                "http_client=%.3fs remote_clients=%.3fs telemetry=%.3fs)",
                time.perf_counter() - shutdown_started,
                timings["search_outcomes"],
                timings["background_tasks"],
                timings["duckdb_drain"],
                timings["duckdb_executor"],
                timings["judge_executor"],
                timings["http_client"],
                timings["remote_clients"],
                timings["telemetry"],
            )

    async def _marked_runner() -> Any:
        nonlocal runner_finished
        try:
            return await _runner()
        finally:
            runner_finished = time.perf_counter()

    lifecycle_started = time.perf_counter()
    try:
        return asyncio.run(_marked_runner())
    finally:
        get_runtime().last_duration_ms = (time.perf_counter() - lifecycle_started) * 1000.0
        lifecycle_finished = time.perf_counter()
        post_runner = lifecycle_finished - runner_finished if runner_finished is not None else None
        if post_runner is None:
            LOGGER.info(
                "CLI asyncio lifecycle finished in %.3fs (post_runner=unavailable)",
                lifecycle_finished - lifecycle_started,
            )
        else:
            LOGGER.info(
                "CLI asyncio lifecycle finished in %.3fs (post_runner=%.3fs)",
                lifecycle_finished - lifecycle_started,
                post_runner,
            )
