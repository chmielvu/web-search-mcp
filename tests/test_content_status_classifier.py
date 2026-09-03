from __future__ import annotations

import json
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fetch_overhaul"


def _load_case(case_id: str) -> tuple[dict, str]:
    meta = json.loads((_FIXTURES / f"{case_id}.json").read_text(encoding="utf-8"))
    md_path = _FIXTURES / f"{case_id}.md"
    markdown = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    return meta, markdown


class TestContentStatusClassifier(unittest.TestCase):
    def test_classifies_browser_error_page(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import classify_markdown

        result = classify_markdown("This site can't be reached. ERR_UNSAFE_PORT")
        self.assertEqual(result.status, "error")
        self.assertIn("err_unsafe_port", result.reason or "")
        self.assertFalse(result.cacheable)

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

    def test_classifies_spa_shell(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import classify_markdown

        result = classify_markdown("This application requires JavaScript.")
        self.assertEqual(result.status, "partial")
        self.assertIn("spa_shell", result.reason or "")

    def test_classifies_short_content(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import classify_markdown

        text = " ".join(["word"] * 40)
        result = classify_markdown(text)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.reason, "too_short")

    def test_mdn_404_docs_are_success(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import classify_markdown

        _, markdown = _load_case("F-c1-mdn")
        result = classify_markdown(markdown)
        self.assertEqual(result.status, "success")
        self.assertNotIn("error_page", result.reason or "")

    def test_wikipedia_authors_are_not_a_wall(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
            wall_from_classification,
        )

        _, markdown = _load_case("F-wall-wiki")
        result = classify_markdown(markdown)
        self.assertEqual(result.status, "success")
        self.assertIsNone(wall_from_classification("success", None, "wikipedia", markdown))

    def test_stackoverflow_author_is_not_a_wall(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
            wall_from_classification,
        )

        _, markdown = _load_case("F-wall-so")
        result = classify_markdown(markdown)
        self.assertEqual(result.status, "success")
        self.assertIsNone(wall_from_classification("success", None, "stackexchange", markdown))

    def test_github_login_form_is_login_wall(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
            wall_from_classification,
        )

        _, markdown = _load_case("F-wall-gh")
        result = classify_markdown(markdown)
        self.assertEqual(result.status, "blocked")
        self.assertTrue((result.reason or "").startswith("login_wall"))
        wall = wall_from_classification("blocked", None, "html", markdown)
        assert wall is not None
        self.assertEqual(wall["kind"], "login")

    def test_short_typed_json_is_success(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import classify_markdown

        _, markdown = _load_case("F-t1-typed-json")
        result = classify_markdown(markdown, source_type="json")
        self.assertEqual(result.status, "success")
        sniffed = classify_markdown(markdown)
        self.assertEqual(sniffed.status, "success")

    def test_wayback_404_mention_is_success(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import classify_markdown

        _, markdown = _load_case("F-k1-mismatch")
        result = classify_markdown(markdown)
        self.assertEqual(result.status, "success")

    def test_bbc_homepage_is_chrome_boilerplate(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import classify_markdown

        _, markdown = _load_case("F-q1-bbc")
        result = classify_markdown(markdown)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.reason, "chrome_boilerplate")


if __name__ == "__main__":
    unittest.main()
