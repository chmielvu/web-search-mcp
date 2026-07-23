"""Regression tests for `PageDuckDBCache.ensure_store_schema` exception handling.

The schema bootstrap creates a `page_cache` table plus a best-effort
`url_hash` index. The implementation must:
- Swallow `duckdb.Error` (concurrent create, older DuckDB variant) and
  log a warning so operators can see what happened.
- Propagate non-duckdb exceptions so real I/O or permission problems
  are not hidden by a broad except.
"""

from __future__ import annotations

import logging
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, str(sys.path[0] + "/../src") if False else "")  # noqa: E402
sys.path.insert(0, str(__file__.rsplit("/tests/", 1)[0] + "/src"))  # noqa: E402

import duckdb  # noqa: E402

from kindly_web_search_mcp_server.cache.page_duckdb import PageDuckDBCache  # noqa: E402


class TestEnsureSchemaExceptionHandling(unittest.TestCase):
    def test_duckdb_error_on_index_creation_is_logged(self) -> None:
        """A `duckdb.Error` on the index call must emit a warning."""
        cache = PageDuckDBCache(db_path=":memory:")
        con = MagicMock()
        # First con.execute = CREATE TABLE call. Second = CREATE INDEX
        # call — raise duckdb.Error here.
        con.execute.side_effect = [
            None,
            duckdb.Error("simulated concurrent create"),
        ]

        with self.assertLogs(
            "kindly_web_search_mcp_server.cache.page_duckdb",
            level=logging.WARNING,
        ) as logs:
            cache.ensure_store_schema(con)

        # The CREATE TABLE call must have run (index failure must not
        # block schema creation).
        self.assertEqual(con.execute.call_count, 2)
        first_sql = con.execute.call_args_list[0].args[0]
        second_sql = con.execute.call_args_list[1].args[0]
        self.assertIn("CREATE TABLE", first_sql)
        self.assertIn("CREATE INDEX", second_sql)

        self.assertTrue(
            any("url_hash" in m for m in logs.output),
            f"expected warning mentioning url_hash; got {logs.output}",
        )

    def test_non_duckdb_exception_propagates(self) -> None:
        """Non-duckdb exceptions during index creation must not be swallowed."""
        cache = PageDuckDBCache(db_path=":memory:")
        con = MagicMock()
        con.execute.side_effect = [
            None,
            RuntimeError("simulated non-duckdb failure"),
        ]

        with self.assertRaises(RuntimeError):
            cache.ensure_store_schema(con)

        # Both calls attempted (no silent swallow).
        self.assertEqual(con.execute.call_count, 2)


if __name__ == "__main__":
    unittest.main()
