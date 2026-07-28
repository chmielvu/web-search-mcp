"""Regression tests for PageSQLiteCache.ensure_store_schema exception handling.

The write-path schema helper must log failures and continue without raising,
so a transient schema problem never fails content storage for callers.
"""

from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.cache.page_sqlite import PageSQLiteCache  # noqa: E402


class TestEnsureSchemaExceptionHandling(unittest.TestCase):
    def test_schema_failure_is_logged_and_swallowed(self) -> None:
        cache = PageSQLiteCache(db_path=":memory:")
        con = MagicMock()

        with (
            patch.object(
                cache,
                "_ensure_schema",
                side_effect=sqlite_error("simulated schema failure"),
            ),
            self.assertLogs(
                "kindly_web_search_mcp_server.cache.page_sqlite",
                level=logging.WARNING,
            ) as logs,
        ):
            cache.ensure_store_schema(con)

        self.assertTrue(
            any("skipped schema initialization/migration" in message for message in logs.output),
            f"expected warning about skipped schema init; got {logs.output}",
        )

    def test_successful_schema_init_is_silent(self) -> None:
        cache = PageSQLiteCache(db_path=":memory:")
        con = MagicMock()

        with patch.object(cache, "_ensure_schema") as ensure:
            cache.ensure_store_schema(con)

        ensure.assert_called_once_with(con)


def sqlite_error(message: str) -> Exception:
    """Return a plain Exception so the test does not depend on sqlite3 specifics."""
    return RuntimeError(message)


if __name__ == "__main__":
    unittest.main()
