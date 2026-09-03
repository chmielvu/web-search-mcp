from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestFetchGuidance(unittest.TestCase):
    def test_short_json_success_skips_login_sentence(self) -> None:
        from kindly_web_search_mcp_server.middleware.query_guidance import _guide_fetch

        message, _, _ = _guide_fetch(
            {
                "mode": "single",
                "results": [
                    {
                        "source_type": "json",
                        "status": "success",
                        "page_content": '{"userId":1}',
                        "window": {"has_more": False},
                        "wall": None,
                    }
                ],
            }
        )
        self.assertNotIn("login/paywall", message)

    def test_wall_warning_only_on_blocked_status(self) -> None:
        from kindly_web_search_mcp_server.middleware.query_guidance import _guide_fetch

        blocked, _, _ = _guide_fetch(
            {
                "mode": "single",
                "results": [
                    {
                        "source_type": "html",
                        "status": "blocked",
                        "page_content": "Sign in",
                        "window": {},
                        "wall": {"kind": "login"},
                    }
                ],
            }
        )
        self.assertIn("Access signal detected: login", blocked)

        success, _, _ = _guide_fetch(
            {
                "mode": "single",
                "results": [
                    {
                        "source_type": "html",
                        "status": "success",
                        "page_content": "Article body " * 40,
                        "window": {},
                        "wall": {"kind": "login"},
                    }
                ],
            }
        )
        self.assertNotIn("Access signal detected", success)

    def test_bulk_mixed_source_types_includes_errors(self) -> None:
        from kindly_web_search_mcp_server.middleware.query_guidance import _guide_fetch

        message, _, _ = _guide_fetch(
            {
                "mode": "bulk",
                "total_requested": 2,
                "results": [
                    {"status": "success", "source_type": "html"},
                    {"status": "error", "source_type": "json"},
                ],
            }
        )
        self.assertIn("Mixed source_types:", message)
        self.assertIn("html", message)
        self.assertIn("json", message)
        self.assertNotIn("All fetched from", message)


if __name__ == "__main__":
    unittest.main()
