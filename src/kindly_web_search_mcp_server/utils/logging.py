from __future__ import annotations

import logging
import os


def configure_logging(*, level: int | str | None = None) -> None:
    """
    Configure logging defaults for both local runs and MCP stdio hosts.

    Goals:
    - Avoid noisy third-party logs during tool execution (especially `httpx` request logs).
    - Keep configuration idempotent so hosts can override it safely.
    - Install process log handler (QueueHandler → QueueListener → BatchDuckDBLogHandler)
      for centralized DuckDB log storage with 48h TTL.
    """
    root = logging.getLogger()
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO")
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    # Only set up basicConfig if nothing configured yet (common for scripts).
    if not root.handlers:
        logging.basicConfig(level=level)
    root.setLevel(level)

    # Silence common noisy libraries unless the host explicitly configures them.
    noisy_loggers = (
        "httpx",
        "httpcore",
        "urllib3",
        "asyncio",
        "nodriver",
        "undetected_chromedriver",
        "crawl4ai",
        "playwright",
        "patchright",
        "LiteLLM",
        "litellm",
        "primp",
    )
    for name in noisy_loggers:
        # `asyncio` can emit noisy warnings about slow callbacks in some environments.
        level = logging.ERROR if name == "asyncio" else logging.WARNING
        logging.getLogger(name).setLevel(level)

    # Install process log handler (non-blocking, writes to DuckDB).
    # Safe to call multiple times — _install_process_logging_guard prevents duplicates.
    _install_process_logging()


# Guard so we only install once even if configure_logging() is called multiple times.
_process_logging_installed = False


def _install_process_logging() -> None:
    global _process_logging_installed
    if _process_logging_installed:
        return

    from ..settings import settings as _settings  # noqa: PLC0415

    if not getattr(_settings, "process_logs_enabled", True):
        return

    db_path = getattr(_settings, "process_logs_duckdb_path", "")
    if not db_path:
        return

    ttl_hours = getattr(_settings, "process_logs_ttl_hours", 48)

    result = _install_duckdb_handler(db_path=db_path, ttl_hours=ttl_hours)
    if result is not None:
        handler, listener = result
        # Store references so tests/stats can access them.
        _process_log_handler = handler
        _process_log_listener = listener
        _process_logging_installed = True


def _install_duckdb_handler(db_path: str, ttl_hours: int) -> tuple | None:
    """Try to install the DuckDB log handler. Returns (handler, listener) or None."""
    try:
        from .duckdb_log_handler import install_process_logging as _install  # noqa: PLC0415

        return _install(db_path=db_path, ttl_hours=ttl_hours)
    except Exception:  # noqa: BLE001
        # Logging config failures must never crash the application.
        return None
