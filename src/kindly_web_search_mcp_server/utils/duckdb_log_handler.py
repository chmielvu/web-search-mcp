"""Custom logging handler that batches records and writes them to DuckDB.

Designed for the web-search-mcp server: captures ALL Python process logs,
stores them in a centralized DuckDB database with 48-hour TTL.

Architecture:
    Python loggers → QueueHandler → queue.Queue → QueueListener
    → BatchDuckDBLogHandler (BufferingHandler, capacity=100)
    → DuckDB batch INSERT (executemany)

    Every 50 flushes: DELETE old rows + CHECKPOINT (TTL cleanup).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import queue
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


class BatchDuckDBLogHandler(logging.handlers.BufferingHandler):
    """Buffers log records and flushes them to DuckDB in batches.

    Subclass of BufferingHandler — flush() is called automatically when
    the buffer reaches ``capacity`` or when a CRITICAL record is emitted.

    Thread safety: flush() is called from the listener thread (via
    BufferingHandler), not from the emitting thread. DuckDB connections
    are not thread-safe, so we create a new connection per flush.
    """

    def __init__(
        self,
        db_path: str,
        capacity: int = 100,
        *,
        ttl_hours: int = 48,
    ) -> None:
        super().__init__(capacity)
        self._db_path = str(Path(db_path).resolve())
        self._ttl_hours = ttl_hours
        self._flush_count = 0
        self._total_inserted = 0
        self._closed = False

        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    # ── schema ──────────────────────────────────────────────────────

    @staticmethod
    def _connect(db_path: str) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(db_path)

    def _ensure_schema(self) -> None:
        con = self._connect(self._db_path)
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS process_logs (
                    log_id        VARCHAR PRIMARY KEY,
                    recorded_at   TIMESTAMP NOT NULL,
                    logged_at     TIMESTAMP NOT NULL,
                    pid           INTEGER NOT NULL,
                    logger_name   VARCHAR NOT NULL,
                    level         VARCHAR NOT NULL,
                    message       VARCHAR NOT NULL,
                    module        VARCHAR,
                    func_name     VARCHAR,
                    lineno        INTEGER,
                    thread_name   VARCHAR,
                    exception     VARCHAR,
                    trace_id      VARCHAR,
                    span_id       VARCHAR,
                    payload_json  VARCHAR
                );
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_logs_time   ON process_logs (recorded_at);"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_logs_level  ON process_logs (level);"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_logs_logger ON process_logs (logger_name);"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_logs_trace  ON process_logs (trace_id);"
            )
        finally:
            con.close()

    # ── OTEL helpers ────────────────────────────────────────────────

    @staticmethod
    def _current_trace_context() -> dict[str, str]:
        try:
            from opentelemetry import trace as otel_trace  # noqa: PLC0415

            span = otel_trace.get_current_span()
            if span and span.is_recording():
                ctx = span.get_span_context()
                return {
                    "trace_id": format(ctx.trace_id, "032x"),
                    "span_id": format(ctx.span_id, "016x"),
                }
        except Exception:  # noqa: BLE001
            pass
        return {}

    # ── record extraction ───────────────────────────────────────────

    @staticmethod
    def _extract_record(record: logging.LogRecord) -> dict[str, Any]:
        """Extract a dict suitable for DuckDB insertion from a LogRecord."""
        exc_text: str | None = None
        if record.exc_info and record.exc_info[0] is not None:
            exc_text = "".join(
                traceback.format_exception(*record.exc_info)
            )

        # Serialize any extra kwargs that aren't part of standard LogRecord
        payload: dict[str, Any] = {}
        standard_attrs = logging.LogRecord(
            "", 0, "", 0, "", (), None
        ).__dict__.keys()
        for key, value in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                try:
                    json.dumps(value, default=str)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = str(value)

        return {
            "log_id": uuid.uuid4().hex,
            "recorded_at": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ),
            "logged_at": datetime.now(tz=timezone.utc),
            "pid": record.process,
            "logger_name": record.name,
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "func_name": record.funcName,
            "lineno": record.lineno,
            "thread_name": record.threadName,
            "exception": exc_text,
            "payload_json": json.dumps(payload, default=str) if payload else None,
        }

    # ── flush ───────────────────────────────────────────────────────

    def flush(self) -> None:
        """Drain the buffer and insert all records into DuckDB."""
        if self._closed:
            return

        buf = self.buffer
        self.buffer = []  # reset before insert so emit() can refill

        if not buf:
            return

        records = [self._extract_record(r) for r in buf]
        trace_ctx = self._current_trace_context()

        con = self._connect(self._db_path)
        try:
            con.executemany(
                """
                INSERT OR IGNORE INTO process_logs
                    (log_id, recorded_at, logged_at, pid, logger_name,
                     level, message, module, func_name, lineno,
                     thread_name, exception, trace_id, span_id, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r["log_id"],
                        r["recorded_at"],
                        r["logged_at"],
                        r["pid"],
                        r["logger_name"],
                        r["level"],
                        r["message"],
                        r["module"],
                        r["func_name"],
                        r["lineno"],
                        r["thread_name"],
                        r["exception"],
                        trace_ctx.get("trace_id"),
                        trace_ctx.get("span_id"),
                        r["payload_json"],
                    )
                    for r in records
                ],
            )
            self._total_inserted += len(records)
            self._flush_count += 1
        except Exception:  # noqa: BLE001
            pass  # don't let insert errors crash the logging path
        finally:
            con.close()

        # Periodic TTL cleanup
        self._maybe_run_ttl_cleanup()

    def _maybe_run_ttl_cleanup(self) -> None:
        """Run TTL cleanup every 50 flushes (~5000 rows at capacity=100)."""
        if self._flush_count % 50 == 0:
            con = self._connect(self._db_path)
            try:
                con.execute(
                    f"""
                    DELETE FROM process_logs
                    WHERE recorded_at < now() - INTERVAL '{self._ttl_hours} hours';
                    """
                )
                con.execute("CHECKPOINT;")
            except Exception:  # noqa: BLE001
                pass
            finally:
                con.close()

    # ── hook into BufferingHandler.shouldFlush ──────────────────────

    def shouldFlush(self, record: logging.LogRecord) -> bool:
        """Flush when buffer is CRITICAL or when buffer is full."""
        if record.levelno >= logging.CRITICAL:
            return True
        return super().shouldFlush(record)

    # ── stats ───────────────────────────────────────────────────────

    @property
    def stats(self) -> dict[str, int]:
        return {
            "total_inserted": self._total_inserted,
            "flush_count": self._flush_count,
            "buffer_pending": len(self.buffer),
        }

    # ── close ───────────────────────────────────────────────────────

    def close(self) -> None:
        """Flush remaining buffer and mark as closed."""
        self._closed = True
        self.flush()
        super().close()


def install_process_logging(
    db_path: str = "",
    ttl_hours: int = 48,
    capacity: int = 100,
    queue_maxsize: int = 10000,
) -> tuple[BatchDuckDBLogHandler, logging.handlers.QueueListener] | None:
    """Install a QueueHandler + QueueListener + BatchDuckDBLogHandler on the root logger.

    Returns (handler, listener) so the caller can stop them on shutdown.
    Returns None if process logging is disabled via settings.

    Usage::

        handler, listener = install_process_logging(db_path=".../logs/process_logs.duckdb")
        # ... server runs ...
        listener.stop()
    """
    settings = _load_settings()
    if not settings.get("process_logs_enabled", True):
        return None

    resolved_db_path = db_path or settings.get(
        "process_logs_duckdb_path",
        "",
    )
    if not resolved_db_path:
        return None

    resolved_ttl = ttl_hours or settings.get("process_logs_ttl_hours", 48)

    # Force root logger to emit everything; handlers do the level filtering.
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(
        maxsize=queue_maxsize
    )
    handler = BatchDuckDBLogHandler(
        db_path=resolved_db_path,
        capacity=capacity,
        ttl_hours=resolved_ttl,
    )
    listener = logging.handlers.QueueListener(
        log_queue,
        handler,
        respect_handler_level=True,
    )
    queue_handler = logging.handlers.QueueHandler(log_queue)

    root.addHandler(queue_handler)
    listener.start()

    return handler, listener


def _load_settings() -> dict[str, Any]:
    """Lazy-load settings to avoid import cycles."""
    try:
        from ..settings import settings as s  # noqa: PLC0415

        return {
            "process_logs_enabled": getattr(s, "process_logs_enabled", True),
            "process_logs_duckdb_path": getattr(s, "process_logs_duckdb_path", ""),
            "process_logs_ttl_hours": getattr(s, "process_logs_ttl_hours", 48),
        }
    except Exception:  # noqa: BLE001
        return {}
