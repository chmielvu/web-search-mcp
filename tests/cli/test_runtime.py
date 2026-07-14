from __future__ import annotations

import asyncio
import logging
import re
import threading
import time

from kindly_web_search_mcp_server.cli.runtime import run_cli_async


def _patch_cleanup(monkeypatch, calls: list[str], *, fail_outcomes: bool = False) -> None:
    from kindly_web_search_mcp_server.analytics import async_writes
    from kindly_web_search_mcp_server.search import outcomes
    from kindly_web_search_mcp_server.telemetry import init as telemetry_init
    from kindly_web_search_mcp_server.utils import background_tasks, http_client

    async def drain_search_outcomes(_timeout: float) -> None:
        calls.append("search_outcomes")
        if fail_outcomes:
            raise RuntimeError("outcome drain failed")

    async def drain_background_tasks(**_kwargs: object) -> None:
        calls.append("background_tasks")

    def shutdown_duckdb_write_executor(*, wait: bool) -> None:
        assert wait is True
        calls.append("duckdb_executor")

    async def close_http_client() -> None:
        calls.append("http_client")

    def shutdown_telemetry() -> None:
        calls.append("telemetry")

    monkeypatch.setattr(outcomes, "drain_search_outcomes", drain_search_outcomes)
    monkeypatch.setattr(background_tasks, "drain_background_tasks", drain_background_tasks)
    monkeypatch.setattr(
        async_writes,
        "shutdown_duckdb_write_executor",
        shutdown_duckdb_write_executor,
    )
    monkeypatch.setattr(http_client, "close_http_client", close_http_client)
    monkeypatch.setattr(telemetry_init, "shutdown_telemetry", shutdown_telemetry)


def test_run_cli_async_reports_post_runner_executor_shutdown(monkeypatch, caplog) -> None:
    calls: list[str] = []
    _patch_cleanup(monkeypatch, calls)
    gate = threading.Event()
    from kindly_web_search_mcp_server.telemetry import init as telemetry_init

    def finish_telemetry() -> None:
        calls.append("telemetry")
        gate.set()

    monkeypatch.setattr(telemetry_init, "shutdown_telemetry", finish_telemetry)

    def executor_work() -> None:
        assert gate.wait(timeout=1.0)
        time.sleep(0.1)

    async def command() -> object:
        asyncio.get_running_loop().run_in_executor(None, executor_work)
        return calls

    with caplog.at_level(logging.INFO, logger="kindly_web_search_mcp_server.cli.runtime"):
        result = run_cli_async(command())

    assert result is calls
    assert calls == [
        "search_outcomes",
        "background_tasks",
        "duckdb_executor",
        "http_client",
        "telemetry",
    ]
    assert "CLI shutdown finished" in caplog.text
    match = re.search(r"post_runner=(\d+\.\d+)s", caplog.text)
    assert match is not None
    assert float(match.group(1)) >= 0.05


def test_run_cli_async_continues_cleanup_after_failure(monkeypatch, caplog) -> None:
    calls: list[str] = []
    _patch_cleanup(monkeypatch, calls, fail_outcomes=True)

    async def command() -> str:
        return "result"

    with caplog.at_level(logging.INFO, logger="kindly_web_search_mcp_server.cli.runtime"):
        result = run_cli_async(command())

    assert result == "result"
    assert calls == [
        "search_outcomes",
        "background_tasks",
        "duckdb_executor",
        "http_client",
        "telemetry",
    ]
    assert "Failed to drain search outcomes: outcome drain failed" in caplog.text
    assert "CLI shutdown finished" in caplog.text
