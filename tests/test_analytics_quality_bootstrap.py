from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestAnalyticsQualityBootstrap(unittest.TestCase):
    def test_compute_search_quality_bootstraps_empty_database(self) -> None:
        try:
            import duckdb
        except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
            self.fail(f"duckdb must be available for analytics tests: {exc}")

        from kindly_web_search_mcp_server.analytics.quality_metrics import (
            compute_search_quality,
        )

        db_path = Path(self._testMethodName).with_suffix(".duckdb")
        if db_path.exists():
            db_path.unlink()

        metrics = compute_search_quality("run-1", db_path=str(db_path))

        self.assertEqual(metrics["total_final_results"], 0)
        self.assertEqual(metrics["provider_count"], 0)

        con = duckdb.connect(str(db_path), read_only=True)
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        row_count = con.execute(
            "SELECT COUNT(*) FROM search_quality_scores WHERE run_key = ?",
            ["run-1"],
        ).fetchone()[0]
        con.close()

        self.assertIn("search_runs", tables)
        self.assertIn("search_quality_scores", tables)
        self.assertEqual(row_count, 1)


if __name__ == "__main__":
    unittest.main()
