from __future__ import annotations

import logging
import sqlite3

from kindly_web_search_mcp_server.utils.sqlite_log_handler import BatchSQLiteLogHandler


def test_close_flushes_buffered_records(tmp_path) -> None:
    db_path = tmp_path / "process_logs.sqlite"
    handler = BatchSQLiteLogHandler(str(db_path), capacity=100)
    handler.emit(logging.LogRecord("probe", logging.INFO, "probe.py", 1, "buffered", (), None))

    handler.close()

    with sqlite3.connect(db_path) as connection:
        persisted = connection.execute("SELECT count(*) FROM process_logs").fetchone()[0]
    assert persisted == 1
