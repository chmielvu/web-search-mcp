from __future__ import annotations

import logging
import logging.handlers
import queue
import sqlite3

from kindly_web_search_mcp_server.utils.sqlite_log_handler import (
    BatchSQLiteLogHandler,
    TracebackPreservingQueueHandler,
)


def test_close_flushes_buffered_records(tmp_path) -> None:
    db_path = tmp_path / "process_logs.sqlite"
    handler = BatchSQLiteLogHandler(str(db_path), capacity=100)
    handler.emit(logging.LogRecord("probe", logging.INFO, "probe.py", 1, "buffered", (), None))

    handler.close()

    with sqlite3.connect(db_path) as connection:
        persisted = connection.execute("SELECT count(*) FROM process_logs").fetchone()[0]
    assert persisted == 1


def _emit_and_close(db_path: str, records: list[logging.LogRecord], capacity: int = 1) -> None:
    handler = BatchSQLiteLogHandler(db_path, capacity=capacity)
    for record in records:
        handler.emit(record)
    handler.close()


def test_flush_populates_fts_index(tmp_path) -> None:
    db_path = tmp_path / "process_logs.sqlite"
    _emit_and_close(
        db_path,
        [
            logging.LogRecord(
                "probe", logging.WARNING, "probe.py", 1, "redis connection lost", (), None
            )
        ],
    )

    with sqlite3.connect(db_path) as connection:
        hits = connection.execute(
            "SELECT count(*) FROM process_logs_fts WHERE process_logs_fts MATCH ?",
            ("redis",),
        ).fetchone()[0]
        indexed = connection.execute("SELECT count(*) FROM process_logs_fts_idx").fetchone()[0]
    assert indexed > 0
    assert hits == 1


def test_schema_creation_backfills_fts_from_existing_rows(tmp_path) -> None:
    db_path = tmp_path / "process_logs.sqlite"
    # Seed the content table as if written by a previous handler instance
    # (or the legacy DuckDB handler) before FTS backfill existed.
    handler = BatchSQLiteLogHandler(str(db_path), capacity=100)
    handler.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO process_logs (log_id, recorded_at, logged_at, pid, logger_name,"
            " level, message) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "seed1",
                "2026-07-01T10:00:00.000000+00:00",
                "2026-07-01T10:00:00.000000+00:00",
                1,
                "probe",
                "WARNING",
                "orphan row with uniquetoken",
            ),
        )
        # count(*) on an external-content FTS table mirrors the content table;
        # the index itself is only observable via MATCH / the shadow index.
        before = connection.execute(
            "SELECT count(*) FROM process_logs_fts WHERE process_logs_fts MATCH ?",
            ("uniquetoken",),
        ).fetchone()[0]
        before_idx = connection.execute("SELECT count(*) FROM process_logs_fts_idx").fetchone()[0]

    # A new handler on the same path runs the startup backfill.
    handler = BatchSQLiteLogHandler(str(db_path), capacity=100)
    handler.close()

    with sqlite3.connect(db_path) as connection:
        after = connection.execute(
            "SELECT count(*) FROM process_logs_fts WHERE process_logs_fts MATCH ?",
            ("uniquetoken",),
        ).fetchone()[0]
        after_idx = connection.execute("SELECT count(*) FROM process_logs_fts_idx").fetchone()[0]
    assert before == 0
    assert before_idx == 0
    assert after == 1
    assert after_idx > 0


def test_ttl_cleanup_deletes_old_rows_but_keeps_recent(tmp_path) -> None:
    db_path = tmp_path / "process_logs.sqlite"
    handler = BatchSQLiteLogHandler(str(db_path), capacity=1, ttl_hours=48)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO process_logs (log_id, recorded_at, logged_at, pid, logger_name,"
            " level, message) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "stale",
                "2026-07-01T10:00:00.000000+00:00",
                "2026-07-01T10:00:00.000000+00:00",
                1,
                "probe",
                "WARNING",
                "stale row",
            ),
        )
    # Force the periodic cleanup to run on the next flush.
    handler._flush_count = 49
    handler.emit(logging.LogRecord("probe", logging.WARNING, "probe.py", 1, "fresh row", (), None))
    handler.close()

    with sqlite3.connect(db_path) as connection:
        stale = connection.execute(
            "SELECT count(*) FROM process_logs WHERE log_id = 'stale'"
        ).fetchone()[0]
        fresh = connection.execute(
            "SELECT count(*) FROM process_logs WHERE message = 'fresh row'"
        ).fetchone()[0]
        stale_fts = connection.execute(
            "SELECT count(*) FROM process_logs_fts WHERE process_logs_fts MATCH ?",
            ("stale row",),
        ).fetchone()[0]
    assert stale == 0
    assert fresh == 1
    assert stale_fts == 0


def test_exception_text_survives_queue_and_is_persisted(tmp_path) -> None:
    db_path = tmp_path / "process_logs.sqlite"
    handler = BatchSQLiteLogHandler(str(db_path), capacity=100)
    log_queue: queue.Queue[logging.LogRecord] = queue.Queue()
    queue_handler = TracebackPreservingQueueHandler(log_queue)
    listener = logging.handlers.QueueListener(log_queue, handler)
    listener.start()
    logger = logging.getLogger("probe")
    logger.setLevel(logging.INFO)
    logger.addHandler(queue_handler)
    try:
        try:
            raise ValueError("boom")
        except ValueError:
            logger.error("failed", exc_info=True)
    finally:
        logger.removeHandler(queue_handler)
        listener.stop()
    handler.close()

    with sqlite3.connect(db_path) as connection:
        exc = connection.execute(
            "SELECT exception FROM process_logs WHERE message LIKE 'failed%'"
        ).fetchone()[0]
    assert exc is not None
    assert "ValueError: boom" in exc
    assert "Traceback" in exc
