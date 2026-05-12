from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestContentStatusClassifier(unittest.TestCase):
    def test_classifies_browser_error_page(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import classify_markdown

        result = classify_markdown("This site can't be reached. ERR_UNSAFE_PORT")
        self.assertEqual(result.status, "error")
        self.assertEqual(result.reason, "browser_error_page")

    def test_classifies_blocked_page(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import classify_markdown

        result = classify_markdown("Access denied. Please verify you are human with captcha.")
        self.assertEqual(result.status, "blocked")

    def test_classifies_successful_content(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import classify_markdown

        text = " ".join(["meaningful"] * 80)
        result = classify_markdown(text)
        self.assertEqual(result.status, "success")
        self.assertTrue(result.cacheable)


if __name__ == "__main__":
    unittest.main()

