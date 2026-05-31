from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestContentWindowing(unittest.TestCase):
    def test_slice_prefers_paragraph_boundary_and_emits_notice(self) -> None:
        from kindly_web_search_mcp_server.content.windowing import slice_content

        text = "First paragraph.\n\nSecond paragraph that should be cut."
        result = slice_content(text, offset=0, length=20)

        self.assertEqual(result.content, "First paragraph.")
        self.assertTrue(result.window.has_more)
        self.assertEqual(result.window.next_offset, len("First paragraph."))
        self.assertIsNotNone(result.window.continuation_notice)
        self.assertIn("paragraph", result.window.continuation_notice or "")

    def test_slice_returns_next_offset_when_more_content_exists(self) -> None:
        from kindly_web_search_mcp_server.content.windowing import slice_content

        result = slice_content("abcdefghij", offset=2, length=4)
        self.assertEqual(result.content, "cdef")
        self.assertTrue(result.window.has_more)
        self.assertEqual(result.window.next_offset, 6)

    def test_slice_handles_offset_beyond_end(self) -> None:
        from kindly_web_search_mcp_server.content.windowing import slice_content

        result = slice_content("abc", offset=100, length=10)
        self.assertEqual(result.content, "")
        self.assertFalse(result.window.has_more)
        self.assertIsNone(result.window.next_offset)


if __name__ == "__main__":
    unittest.main()
