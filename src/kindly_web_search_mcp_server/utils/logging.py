from __future__ import annotations

import logging
import os
from typing import Any


import json
import sys
from datetime import UTC, datetime
from opentelemetry import trace


class JsonStderrLogFormatter(logging.Formatter):
    """Format log records as single-line JSON (JSONL) for stderr stream processing by jq, Vector, or Fluent Bit."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        span = trace.get_current_span()
        if span and span.get_span_context().is_valid:
            ctx = span.get_span_context()
            payload["trace_id"] = format(ctx.trace_id, "032x")
            payload["span_id"] = format(ctx.span_id, "016x")
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        ctx = getattr(record, "context", None)
        if isinstance(ctx, dict):
            payload["context"] = ctx
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(
    *,
    level: int | str | None = None,
    log_format: str | None = None,
) -> None:
    """
    Configure logging defaults for both local runs and MCP stdio hosts.

    Goals:
    - Avoid noisy third-party logs during tool execution.
    - Keep configuration idempotent so hosts can override it safely.
    - Support `log_format='json'` (or env `LOG_FORMAT=json`) for single-line JSONL stderr output
      compatible with `jq`, `Vector` VRL, `Fluent Bit`, and `Fluentd`.
    - Install process log handler (QueueHandler -> QueueListener -> BatchDuckDBLogHandler).
    """
    root = logging.getLogger()
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO")
    if isinstance(level, str):
        resolved_level = getattr(logging, level.upper(), logging.INFO)
    else:
        resolved_level = level

    if log_format is None:
        log_format = os.environ.get(
            "LOG_FORMAT", os.environ.get("WEB_SEARCH_CLI_LOG_FORMAT", "text")
        ).lower()

    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        if log_format == "json":
            handler.setFormatter(JsonStderrLogFormatter())
        root.addHandler(handler)
    else:
        for handler in root.handlers:
            if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stderr:
                if log_format == "json":
                    handler.setFormatter(JsonStderrLogFormatter())

    root.setLevel(resolved_level)
    # Silence common noisy libraries unless the host explicitly configures them.
    # `rustls`, `h2`, `hyper_util`, and `cookie_store` produce ~110 lines of
    # TLS / HTTP-2 / cookie chatter per HTTP request at DEBUG. Drop them to
    # WARNING so they stay available for opt-in debugging without drowning
    # the transcript at the default level.
    noisy_loggers = (
        "httpx",
        "httpcore",
        "urllib3",
        "asyncio",
        "primp",
        "rustls",
        "h2",
        "hyper_util",
        "cookie_store",
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

    db_path = getattr(_settings, "process_logs_sqlite_path", None) or getattr(
        _settings, "process_logs_duckdb_path", ""
    )
    if not db_path:
        return

    ttl_hours = getattr(_settings, "process_logs_ttl_hours", 48)

    result = _install_sqlite_handler(db_path=db_path, ttl_hours=ttl_hours)
    if result is not None:
        handler, listener = result
        # Store references so tests/stats can access them.
        _process_log_handler = handler
        _process_log_listener = listener
        _process_logging_installed = True


def _install_sqlite_handler(db_path: str, ttl_hours: int) -> tuple | None:
    """Try to install the SQLite log handler. Returns (handler, listener) or None."""
    try:
        from .sqlite_log_handler import install_process_logging as _install  # noqa: PLC0415

        return _install(db_path=db_path, ttl_hours=ttl_hours)
    except Exception:  # noqa: BLE001
        # Logging config failures must never crash the application.
        return None
