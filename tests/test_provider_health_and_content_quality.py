from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestContentStatusClassifier(unittest.TestCase):
    def test_classifies_browser_error_page(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        result = classify_markdown("This site can't be reached. ERR_UNSAFE_PORT")
        self.assertEqual(result.status, "error")
        self.assertIn("err_unsafe_port", result.reason or "")

    def test_classifies_blocked_page(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        result = classify_markdown("Access denied. Please verify you are human with captcha.")
        self.assertEqual(result.status, "blocked")
        self.assertIn("access_blocked:", result.reason or "")
        self.assertIn("access denied", result.reason or "")

    def test_classifies_login_wall(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        content = "Welcome to Example.com. Sign in to continue reading this article."
        content += " meaningful text " * 10  # Add enough words to pass short check
        result = classify_markdown(content)
        self.assertEqual(result.status, "blocked")
        self.assertIn("login_wall", result.reason or "")

    def test_classifies_paywall(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        content = "Subscribe to read the full article. You've reached your free article limit."
        content += " filler text " * 10
        result = classify_markdown(content)
        self.assertEqual(result.status, "blocked")
        self.assertIn("paywall", result.reason or "")

    def test_classifies_cloudflare_block(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        content = "Checking your browser before accessing the site. Cloudflare."
        content += " please wait " * 10
        result = classify_markdown(content)
        self.assertEqual(result.status, "blocked")

    def test_classifies_http_error_page(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        content = "404 Not Found. The requested URL was not found on this server."
        content += " nginx " * 10
        result = classify_markdown(content)
        self.assertEqual(result.status, "error")

    def test_classifies_500_error(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        content = "500 Internal Server Error. Please try again later."
        content += " troubleshooting tips " * 10
        result = classify_markdown(content)
        self.assertEqual(result.status, "error")

    def test_classifies_successful_content(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        text = " ".join(["meaningful"] * 80)
        result = classify_markdown(text)
        self.assertEqual(result.status, "success")
        self.assertTrue(result.cacheable)

    def test_classifies_redirect_only(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        result = classify_markdown("https://example.com/actual-page")
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.reason, "redirect_only")

    def test_classifies_redirect_notice(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        result = classify_markdown("Redirecting to https://example.com/destination")
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.reason, "redirect_only")

    def test_classifies_garbled_content(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        # High ratio of null bytes and control chars (>15% non-printable)
        content = "\x00\x01\x02\x03\x04\x05" * 5 + " a" * 30 + " b" * 5
        result = classify_markdown(content)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.reason, "garbled_content")

    def test_classifies_empty_content(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        result = classify_markdown("")
        self.assertEqual(result.status, "error")
        self.assertEqual(result.reason, "empty_content")

    def test_classifies_too_short(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        result = classify_markdown("short content")
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.reason, "too_short")


class TestContentQualityScoring(unittest.TestCase):
    def test_good_content_scores_high(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_quality,
        )

        text = " ".join(["meaningful"] * 100)
        score = classify_quality(text)
        self.assertGreater(score, 0.5)

    def test_empty_content_scores_zero(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_quality,
        )

        self.assertEqual(classify_quality(""), 0.0)
        self.assertEqual(classify_quality("   "), 0.0)

    def test_blocked_page_scores_low(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_quality,
        )

        content = "Access denied. Verify you are human." + " ignored text " * 50
        score = classify_quality(content)
        self.assertLess(score, 0.5)

    def test_very_short_scores_low(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_quality,
        )

        score = classify_quality("just ten words of content here okay")
        self.assertLess(score, 0.5)

    def test_garbled_text_scores_low(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_quality,
        )

        content = "\x00\x01\x02" * 10 + " a" * 30
        score = classify_quality(content)
        self.assertLess(score, 0.5)


if __name__ == "__main__":
    unittest.main()
