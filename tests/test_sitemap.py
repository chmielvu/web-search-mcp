"""Tests for semantic sitemap generation."""

from __future__ import annotations

import unittest


class TestExtractSectionsFromMarkdown(unittest.TestCase):
    def test_extracts_atx_headings(self) -> None:
        from kindly_web_search_mcp_server.content.sitemap import (
            _extract_sections_from_markdown,
        )

        md = """# Title

Intro text.

## Section A

Content A.

### Subsection A1

Content A1.

## Section B

Content B.
"""
        sections = _extract_sections_from_markdown(md)
        self.assertEqual(len(sections), 4)
        self.assertEqual(sections[0].level, 1)
        self.assertEqual(sections[0].heading, "Title")
        self.assertEqual(sections[1].level, 2)
        self.assertEqual(sections[1].heading, "Section A")
        self.assertEqual(sections[2].level, 3)
        self.assertEqual(sections[2].heading, "Subsection A1")
        self.assertEqual(sections[3].level, 2)
        self.assertEqual(sections[3].heading, "Section B")

    def test_text_preview_truncated(self) -> None:
        from kindly_web_search_mcp_server.content.sitemap import (
            _extract_sections_from_markdown,
        )

        md = "## Big Section\n\n" + "word " * 200
        sections = _extract_sections_from_markdown(md, preview_chars=50)
        self.assertEqual(len(sections), 1)
        self.assertLessEqual(len(sections[0].text_preview), 55)  # 50 + "..."
        self.assertTrue(sections[0].text_preview.endswith("..."))

    def test_no_headings_returns_empty(self) -> None:
        from kindly_web_search_mcp_server.content.sitemap import (
            _extract_sections_from_markdown,
        )

        md = "Just plain text\nwith no headings at all."
        sections = _extract_sections_from_markdown(md)
        self.assertEqual(sections, [])

    def test_empty_markdown(self) -> None:
        from kindly_web_search_mcp_server.content.sitemap import (
            _extract_sections_from_markdown,
        )

        sections = _extract_sections_from_markdown("")
        self.assertEqual(sections, [])

    def test_preview_chars_respected(self) -> None:
        from kindly_web_search_mcp_server.content.sitemap import (
            _extract_sections_from_markdown,
        )

        md = "## Short\n\nHello world."
        sections = _extract_sections_from_markdown(md, preview_chars=5)
        self.assertEqual(len(sections), 1)
        self.assertIn("Hello", sections[0].text_preview)


class TestExtractTitleFromMarkdown(unittest.TestCase):
    def test_extracts_first_h1(self) -> None:
        from kindly_web_search_mcp_server.content.sitemap import (
            _extract_title_from_markdown,
        )

        md = "# My Page Title\n\nSome content."
        self.assertEqual(_extract_title_from_markdown(md), "My Page Title")

    def test_skips_h2_h3(self) -> None:
        from kindly_web_search_mcp_server.content.sitemap import (
            _extract_title_from_markdown,
        )

        md = "## Not a title\n# Actual Title"
        self.assertEqual(_extract_title_from_markdown(md), "Actual Title")

    def test_fallback_to_first_non_empty_line(self) -> None:
        from kindly_web_search_mcp_server.content.sitemap import (
            _extract_title_from_markdown,
        )

        md = "\n\nHello World\nMore content"
        self.assertEqual(_extract_title_from_markdown(md), "Hello World")

    def test_empty_returns_empty_string(self) -> None:
        from kindly_web_search_mcp_server.content.sitemap import (
            _extract_title_from_markdown,
        )

        self.assertEqual(_extract_title_from_markdown(""), "")


class TestBuildLlmsTxtMarkdown(unittest.TestCase):
    def test_basic_output(self) -> None:
        from kindly_web_search_mcp_server.content.sitemap import (
            PageSection,
            SitemapPage,
            _build_llms_txt_markdown,
        )

        pages = [
            SitemapPage(
                url="https://example.com/a",
                title="Page A",
                depth=1,
                sections=[
                    PageSection(level=1, heading="Intro", text_preview="..."),
                    PageSection(level=2, heading="Details", text_preview="..."),
                ],
            ),
        ]
        output = _build_llms_txt_markdown(pages, base_url="https://example.com")
        self.assertIn("# https://example.com", output)
        self.assertIn("## Page A", output)
        self.assertIn("URL: https://example.com/a", output)
        self.assertIn("- Intro", output)
        self.assertIn("- Details", output)

    def test_empty_pages(self) -> None:
        from kindly_web_search_mcp_server.content.sitemap import (
            _build_llms_txt_markdown,
        )

        output = _build_llms_txt_markdown([], base_url="https://example.com")
        self.assertIn("# https://example.com", output)


class TestExtractDomain(unittest.TestCase):
    def test_extracts_domain(self) -> None:
        from kindly_web_search_mcp_server.content.sitemap import _extract_domain

        self.assertEqual(
            _extract_domain("https://docs.example.com/guide"),
            "https://docs.example.com",
        )

    def test_extracts_domain_with_port(self) -> None:
        from kindly_web_search_mcp_server.content.sitemap import _extract_domain

        self.assertEqual(
            _extract_domain("http://localhost:8080/docs"),
            "http://localhost:8080",
        )


class TestPathMatching(unittest.TestCase):
    def test_exact_path_match(self) -> None:
        from kindly_web_search_mcp_server.content.sitemap import _path_match_score

        self.assertEqual(
            _path_match_score("https://example.com/a/b", "/a/b"),
            1000,
        )

    def test_partial_prefix_match(self) -> None:
        from kindly_web_search_mcp_server.content.sitemap import _path_match_score

        self.assertEqual(
            _path_match_score("https://example.com/a/b/c", "/a/b"),
            3,
        )

    def test_no_match(self) -> None:
        from kindly_web_search_mcp_server.content.sitemap import _path_match_score

        self.assertEqual(
            _path_match_score("https://example.com/x/y", "/a/b"),
            0,
        )

    def test_language_prefix_detected(self) -> None:
        from kindly_web_search_mcp_server.content.sitemap import (
            _is_preferred_path_variant,
            _NON_DEFAULT_LANG_PREFIXES,
        )

        self.assertTrue("ar" in _NON_DEFAULT_LANG_PREFIXES)
        self.assertFalse(
            _is_preferred_path_variant(
                "https://docs.crewai.com/ar/api-reference",
                "/introduction",
            )
        )
        self.assertTrue(
            _is_preferred_path_variant(
                "https://docs.crewai.com/en/introduction",
                "/introduction",
            )
        )

    def test_sort_prefers_matching_paths(self) -> None:
        from kindly_web_search_mcp_server.content.sitemap import _sort_discovered_urls

        urls = [
            "https://crewai.com/ar/api",
            "https://crewai.com/en/intro",
            "https://crewai.com/intro",
            "https://crewai.com/en/guide",
        ]
        sorted_urls = _sort_discovered_urls(urls, input_url="https://crewai.com/intro")
        # Exact match first
        self.assertEqual(sorted_urls[0], "https://crewai.com/intro")
        # English /en/ prefix before Arabic /ar/
        self.assertTrue(
            sorted_urls.index("https://crewai.com/en/intro")
            < sorted_urls.index("https://crewai.com/ar/api"),
        )


if __name__ == "__main__":
    unittest.main()
