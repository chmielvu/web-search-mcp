"""Custom logging handler that batches records and writes them to SQLite (WAL mode).

Designed for the web-search-mcp server: captures ALL Python process logs,
stores them in a centralized SQLite database with 48-hour TTL.

Architecture:
    Python loggers → QueueHandler → queue.Queue → QueueListener
    → BatchSQLiteLogHandler (BufferingHandler, capacity=100)
    → SQLite batch INSERT (executemany)

    Every 50 flushes: DELETE old rows (TTL cleanup) + FTS index backfill.
    TTL compares with julianday() — SQLite's datetime() returns NULL for the
    ISO-8601 text stored in recorded_at, which silently disabled cleanup.
    The external-content FTS5 table is populated at schema creation and fed
    per-flush via INSERT ... RETURNING rowid (external-content tables never
    auto-populate). TracebackPreservingQueueHandler keeps exception text
    across the queue boundary (stdlib QueueHandler strips exc_info/exc_text).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import queue
import sqlite3
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class BatchSQLiteLogHandler(logging.handlers.BufferingHandler):
    """Buffers log records and flushes them to SQLite in batches.

    Subclass of BufferingHandler — flush() is called automatically when
    the buffer reaches ``capacity`` or when a CRITICAL record is emitted.

    Thread safety: flush() is called from the listener thread (via
    BufferingHandler), not from the emitting thread.
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
    def _connect(db_path: str) -> sqlite3.Connection:
        con = sqlite3.connect(db_path, timeout=10.0)
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        con.execute("PRAGMA busy_timeout=5000;")
        return con

    def _ensure_schema(self) -> None:
        con = self._connect(self._db_path)
        try:
            with con:
                con.execute(
                    """
                    CREATE TABLE IF NOT EXISTS process_logs (
                        log_id        TEXT PRIMARY KEY,
                        recorded_at   TEXT NOT NULL,
                        logged_at     TEXT NOT NULL,
                        pid           INTEGER NOT NULL,
                        logger_name   TEXT NOT NULL,
                        level         TEXT NOT NULL,
                        level_num     INTEGER NOT NULL DEFAULT 0,
                        message       TEXT NOT NULL,
                        module        TEXT,
                        func_name     TEXT,
                        lineno        INTEGER,
                        thread_name   TEXT,
                        exception     TEXT,
                        trace_id      TEXT,
                        span_id       TEXT,
                        environment   TEXT DEFAULT 'production',
                        payload_json  TEXT
                    );
                    """
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_logs_time   ON process_logs (recorded_at);"
                )
                con.execute("CREATE INDEX IF NOT EXISTS idx_logs_level  ON process_logs (level);")
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_logs_logger ON process_logs (logger_name);"
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_logs_trace  ON process_logs (trace_id);"
                )
                con.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS process_logs_fts USING fts5(
                        message,
                        exception,
                        payload_json,
                        content='process_logs',
                        content_rowid='rowid'
                    );
                    """
                )
                # External-content FTS5 tables never auto-populate: rows written
                # before this handler runs (or by the legacy DuckDB handler)
                # would be permanently invisible to MATCH. Backfill on startup.
                con.execute(
                    """
                    INSERT OR IGNORE INTO process_logs_fts
                        (rowid, message, exception, payload_json)
                    SELECT rowid, message, exception, payload_json FROM process_logs;
                    """
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
        """Extract a dict suitable for SQLite insertion from a LogRecord."""
        # TracebackPreservingQueueHandler stashes the formatted traceback on
        # _exc_text because QueueHandler.prepare() strips exc_info/exc_text.
        exc_text = getattr(record, "_exc_text", None)
        if not exc_text and record.exc_info and record.exc_info[0] is not None:
            exc_text = "".join(traceback.format_exception(*record.exc_info))
        elif not exc_text and record.exc_text:
            exc_text = record.exc_text

        # Serialize any extra kwargs that aren't part of standard LogRecord
        payload: dict[str, Any] = {}
        standard_attrs = logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
        for key, value in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                try:
                    json.dumps(value, default=str)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = str(value)

        return {
            "log_id": uuid.uuid4().hex,
            "recorded_at": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "logged_at": datetime.now(tz=timezone.utc).isoformat(),
            "pid": record.process,
            "logger_name": record.name,
            "level": record.levelname,
            "level_num": record.levelno,
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
        """Drain the buffer and insert all records into SQLite."""
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
            with con:
                cur = con.executemany(
                    """
                    INSERT OR IGNORE INTO process_logs
                        (log_id, recorded_at, logged_at, pid, logger_name,
                         level, level_num, message, module, func_name, lineno,
                         thread_name, exception, trace_id, span_id, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING rowid;
                    """,
                    [
                        (
                            r["log_id"],
                            r["recorded_at"],
                            r["logged_at"],
                            r["pid"],
                            r["logger_name"],
                            r["level"],
                            r["level_num"],
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
                inserted = cur.fetchall()
                # Keep the external-content FTS index in sync with new rows
                # (startup backfill covers rows written before this handler).
                if inserted:
                    con.executemany(
                        """
                        INSERT OR IGNORE INTO process_logs_fts
                            (rowid, message, exception, payload_json)
                        SELECT rowid, message, exception, payload_json
                        FROM process_logs WHERE rowid = ?;
                        """,
                        [(row[0],) for row in inserted],
                    )
            self._total_inserted += len(records)
            self._flush_count += 1
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning("SQLite process log flush failed: %s", exc)
        finally:
            con.close()

        # Periodic TTL cleanup
        self._maybe_run_ttl_cleanup()

    def _maybe_run_ttl_cleanup(self) -> None:
        """Run TTL cleanup every 50 flushes (~5000 rows at capacity=100)."""
        if self._flush_count % 50 == 0:
            con = self._connect(self._db_path)
            try:
                with con:
                    # recorded_at is ISO-8601 text (e.g.
                    # 2026-07-23T15:56:44.796264+00:00); SQLite's datetime()
                    # returns NULL for that format and a NULL comparison is
                    # never true, so the old predicate deleted nothing. Parse
                    # with julianday(), which understands ISO-8601, and
                    # compare against the cutoff in days.
                    con.execute(
                        """
                        DELETE FROM process_logs
                        WHERE julianday(recorded_at) < julianday('now') - ?;
                        """,
                        (self._ttl_hours / 24.0,),
                    )
                    # Keep the external-content FTS index in sync: stale
                    # shadow rows would resurrect deleted log entries in
                    # MATCH results otherwise.
                    con.execute(
                        """
                        DELETE FROM process_logs_fts
                        WHERE rowid NOT IN (SELECT rowid FROM process_logs);
                        """
                    )
            except Exception as exc:  # noqa: BLE001
                logging.getLogger(__name__).warning(
                    "SQLite process log TTL cleanup failed: %s", exc
                )
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
            "buffer_size": len(self.buffer),
            "capacity": self.capacity,
            "flush_count": self.flush_count,
            "total_inserted": self.total_inserted,
        }

    @property
    def flush_count(self) -> int:
        return self._flush_count

    @property
    def total_inserted(self) -> int:
        return self._total_inserted

    # ── close ───────────────────────────────────────────────────────

    def close(self) -> None:
        """Flush remaining buffer and mark as closed."""
        if self._closed:
            return
        try:
            self.flush()
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._closed = True
            super().close()


class TracebackPreservingQueueHandler(logging.handlers.QueueHandler):
    """QueueHandler that keeps formatted tracebacks across the queue boundary.

    stdlib QueueHandler.prepare() zaps ``exc_info`` and ``exc_text`` before
    enqueueing (they may not be pickleable), so the SQLite handler on the
    listener side never sees exception text. This subclass formats the
    record first (which fills ``exc_text``), then stashes that text on the
    prepared copy as ``_exc_text`` so it survives the queue. Only records
    that actually carry exception data pay the extra format() call.
    """

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        exc_text: str | None = getattr(record, "exc_text", None)
        if exc_text is None and record.exc_info and record.exc_info[0] is not None:
            self.format(record)
            exc_text = getattr(record, "exc_text", None)
        prepared = super().prepare(record)
        if exc_text:
            prepared._exc_text = exc_text  # type: ignore[attr-defined]
        return prepared


def install_process_logging(
    db_path: str | None = None,
    ttl_hours: int | None = None,
    capacity: int = 100,
) -> tuple[BatchSQLiteLogHandler, logging.handlers.QueueListener] | None:
    """Install a QueueHandler + QueueListener + BatchSQLiteLogHandler on the root logger."""
    settings = _load_settings()
    if not settings.get("process_logs_enabled", True):
        return None

    resolved_db_path = db_path or settings.get(
        "process_logs_sqlite_path",
        settings.get("process_logs_duckdb_path", "duckdb_data/logs/process_logs.sqlite"),
    )
    if not resolved_db_path:
        return None

    resolved_ttl = ttl_hours or settings.get("process_logs_ttl_hours", 48)

    root = logging.getLogger()
    configured_root_level = root.level
    if configured_root_level == logging.NOTSET:
        configured_root_level = logging.INFO

    handler = BatchSQLiteLogHandler(
        db_path=resolved_db_path,
        capacity=capacity,
        ttl_hours=resolved_ttl,
    )
    handler.setLevel(configured_root_level)

    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=10000)
    queue_handler = TracebackPreservingQueueHandler(log_queue)
    queue_handler.setLevel(configured_root_level)

    listener = logging.handlers.QueueListener(
        log_queue,
        handler,
        respect_handler_level=True,
    )
    listener.start()

    root.addHandler(queue_handler)
    logger = logging.getLogger(__name__)
    logger.info("Process logging installed: sqlite_db=%s, ttl=%dh", resolved_db_path, resolved_ttl)

    return handler, listener


def _load_settings() -> dict[str, Any]:
    """Lazy-load settings to avoid import cycles."""
    try:
        from ..settings import settings as s

        return {
            "process_logs_enabled": getattr(s, "process_logs_enabled", True),
            "process_logs_sqlite_path": getattr(s, "process_logs_sqlite_path", None),
            "process_logs_duckdb_path": getattr(s, "process_logs_duckdb_path", ""),
            "process_logs_ttl_hours": getattr(s, "process_logs_ttl_hours", 48),
        }
    except Exception:  # noqa: BLE001
        return {}


# Alias for compatibility during migration
BatchDuckDBLogHandler = BatchSQLiteLogHandler
