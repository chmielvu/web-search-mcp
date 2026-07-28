from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestAnalyticsViews(unittest.TestCase):
    def test_public_ensure_local_views_export_is_wired(self) -> None:
        from kindly_web_search_mcp_server import analytics
        from kindly_web_search_mcp_server.analytics.views import ensure_local_views

        self.assertIs(analytics.ensure_local_views, ensure_local_views)
        self.assertNotIn("append_event", analytics.__all__)


if __name__ == "__main__":
    unittest.main()
