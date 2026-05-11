"""Unit tests for gemini_search_tool.py - Gemini grounding with fallback tier."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.search.gemini_search_tool import (
    GEMINI_GROUNDING_TIER,
    GeminiGroundingResult,
    _classify_gemini_error,
    _is_gemini_model,
    get_system_prompt,
    gemini_search_with_grounding,
)


def _fake_grounding_response() -> SimpleNamespace:
    """Create a fake grounding response for testing."""
    return SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            text="FastMCP docs explain middleware support [1].",
                            thought=False,
                        ),
                    ]
                ),
                grounding_metadata=SimpleNamespace(
                    web_search_queries=["fastmcp middleware docs"],
                    grounding_chunks=[
                        SimpleNamespace(
                            web=SimpleNamespace(
                                uri="https://gofastmcp.com/docs/middleware",
                                title="FastMCP Middleware Docs",
                            )
                        ),
                    ],
                    grounding_supports=[
                        SimpleNamespace(
                            segment=SimpleNamespace(
                                text="FastMCP docs explain middleware support.",
                                start_index=0,
                                end_index=30,
                            ),
                            grounding_chunk_indices=[0],
                        ),
                    ],
                    search_entry_point=SimpleNamespace(
                        rendered_content="<div>Search widget</div>"
                    ),
                ),
            )
        ]
    )


def _create_mock_error(status_code: int) -> Exception:
    """Create a mock error with status_code attribute."""
    exc = Exception(f"API error {status_code}")
    exc.status_code = status_code  # type: ignore[attr-defined]
    return exc


class TestGeminiFallbackTier(unittest.TestCase):
    def test_fallback_tier_order(self) -> None:
        """Verify hardcoded fallback tier order."""
        self.assertEqual(GEMINI_GROUNDING_TIER[0], "gemini-2.5-flash")
        self.assertEqual(GEMINI_GROUNDING_TIER[1], "gemini-2.5-flash-lite")
        self.assertEqual(GEMINI_GROUNDING_TIER[2], "gemma-4-31b-it")
        self.assertEqual(len(GEMINI_GROUNDING_TIER), 3)

    def test_primary_is_gemini_flash(self) -> None:
        """Primary model should be Gemini 2.5 Flash."""
        self.assertTrue(_is_gemini_model(GEMINI_GROUNDING_TIER[0]))
        self.assertTrue(_is_gemini_model(GEMINI_GROUNDING_TIER[1]))
        self.assertFalse(_is_gemini_model(GEMINI_GROUNDING_TIER[2]))


class TestGeminiSystemInstructionHandling(unittest.TestCase):
    def test_gemini_models_accept_system_instruction(self) -> None:
        """Gemini models should be identified for system_instruction."""
        self.assertTrue(_is_gemini_model("gemini-2.5-flash"))
        self.assertTrue(_is_gemini_model("gemini-2.5-flash-lite"))
        self.assertTrue(_is_gemini_model("gemini-1.5-pro"))

    def test_gemma_models_do_not_accept_system_instruction(self) -> None:
        """Gemma models should NOT be identified for system_instruction."""
        self.assertFalse(_is_gemini_model("gemma-4-31b-it"))
        self.assertFalse(_is_gemini_model("gemma-3"))

    def test_system_prompt_includes_date(self) -> None:
        """System prompt should include current date."""
        prompt = get_system_prompt()
        self.assertIn("Today is", prompt)
        # Date format: "May 11, 2026" style
        import re
        date_pattern = r"Today is [A-Z][a-z]+ \d{1,2}, \d{4}"
        self.assertTrue(re.search(date_pattern, prompt) is not None)

    def test_system_prompt_with_research_goal(self) -> None:
        """System prompt should incorporate research goal."""
        goal = "Focus on security vulnerabilities in Log4j"
        prompt = get_system_prompt(research_goal=goal)
        self.assertIn(goal, prompt)

    def test_system_prompt_citation_instructions(self) -> None:
        """System prompt should instruct on inline citations."""
        prompt = get_system_prompt()
        self.assertIn("[1]", prompt)
        self.assertIn("[2]", prompt)
        self.assertIn("cite", prompt.lower())


class TestGeminiErrorClassification(unittest.TestCase):
    def test_rate_limit_error(self) -> None:
        """429 should classify as rate_limit with retry."""
        exc = MagicMock()
        exc.status_code = 429
        error_type, should_fallback, should_retry = _classify_gemini_error(exc)
        self.assertEqual(error_type, "rate_limit")
        self.assertTrue(should_fallback)
        self.assertTrue(should_retry)

    def test_service_unavailable_error(self) -> None:
        """503 should classify as service_unavailable without retry."""
        exc = MagicMock()
        exc.status_code = 503
        error_type, should_fallback, should_retry = _classify_gemini_error(exc)
        self.assertEqual(error_type, "service_unavailable")
        self.assertTrue(should_fallback)
        self.assertFalse(should_retry)

    def test_model_not_found_error(self) -> None:
        """404 should classify as model_not_found without retry."""
        exc = MagicMock()
        exc.status_code = 404
        error_type, should_fallback, should_retry = _classify_gemini_error(exc)
        self.assertEqual(error_type, "model_not_found")
        self.assertTrue(should_fallback)
        self.assertFalse(should_retry)

    def test_unknown_error(self) -> None:
        """Unknown errors should fallback without retry."""
        exc = RuntimeError("something went wrong")
        error_type, should_fallback, should_retry = _classify_gemini_error(exc)
        self.assertEqual(error_type, "unknown")
        self.assertTrue(should_fallback)
        self.assertFalse(should_retry)


class TestGeminiSearchWithGrounding(unittest.IsolatedAsyncioTestCase):
    async def test_gemini_search_returns_grounded_result(self) -> None:
        """Verify successful grounding returns proper result."""
        client = MagicMock()
        client.models.generate_content.return_value = _fake_grounding_response()

        with (
            patch.dict(os.environ, {"KINDLY_GEMINI_API_KEY": "test-key"}, clear=False),
            patch(
                "kindly_web_search_mcp_server.search.gemini_search_tool.get_gemini_client",
                return_value=client,
            ),
        ):
            result = await gemini_search_with_grounding(
                query="fastmcp middleware docs",
                structured_output=False,
            )

        self.assertIsInstance(result, GeminiGroundingResult)
        self.assertEqual(result.query, "fastmcp middleware docs")
        self.assertIn("middleware", result.answer.lower())
        self.assertEqual(result.model_used, "gemini-2.5-flash")
        self.assertEqual(len(result.grounding_chunks), 1)
        self.assertEqual(result.grounding_chunks[0]["url"], "https://gofastmcp.com/docs/middleware")
        self.assertIsNone(result.error)

    async def test_gemini_search_structured_output(self) -> None:
        """Verify structured output request."""
        client = MagicMock()
        response = _fake_grounding_response()
        # Modify response for structured output
        response.candidates[0].content.parts[0].text = (
            '{"executive_summary": "Test", "key_findings": ["finding1"], '
            '"sources": [{"url": "https://example.com"}], "confidence": "high"}'
        )
        client.models.generate_content.return_value = response

        with (
            patch.dict(os.environ, {"KINDLY_GEMINI_API_KEY": "test-key"}, clear=False),
            patch(
                "kindly_web_search_mcp_server.search.gemini_search_tool.get_gemini_client",
                return_value=client,
            ),
        ):
            result = await gemini_search_with_grounding(
                query="test query",
                structured_output=True,
            )

        self.assertTrue(result.structured_output)
        self.assertIsNotNone(result.structured_result)
        self.assertEqual(result.structured_result["executive_summary"], "Test")

    async def test_gemini_search_returns_error_without_key(self) -> None:
        """Should return error when API key not set."""
        with patch(
            "kindly_web_search_mcp_server.search.gemini_search_tool.get_gemini_client",
            return_value=None,
        ):
            result = await gemini_search_with_grounding(
                query="test query",
                structured_output=False,
            )

        self.assertIsNotNone(result.error)
        self.assertIn("KINDLY_GEMINI_API_KEY", result.error)

    async def test_gemini_search_fallback_on_rate_limit(self) -> None:
        """Verify fallback on rate limit error."""
        client = MagicMock()
        # First call (primary) fails with 429
        exc = _create_mock_error(429)
        client.models.generate_content.side_effect = [
            exc,  # First attempt fails
            exc,  # Retry also fails
            _fake_grounding_response(),  # Next tier succeeds
        ]

        with (
            patch.dict(os.environ, {"KINDLY_GEMINI_API_KEY": "test-key"}, clear=False),
            patch(
                "kindly_web_search_mcp_server.search.gemini_search_tool.get_gemini_client",
                return_value=client,
            ),
        ):
            result = await gemini_search_with_grounding(
                query="test query",
                structured_output=False,
            )

        # Should have tried primary, retry failed, then succeeded on next tier
        self.assertEqual(result.model_used, "gemini-2.5-flash-lite")
        self.assertIn("gemini-2.5-flash", result.fallback_chain)

    async def test_gemini_search_fallback_to_gemma(self) -> None:
        """Verify fallback to Gemma when all Gemini models fail."""
        client = MagicMock()
        # Both Gemini models fail
        exc1 = _create_mock_error(503)
        exc2 = _create_mock_error(503)
        # Gemma succeeds
        client.models.generate_content.side_effect = [
            exc1,  # gemini-2.5-flash fails
            exc2,  # gemini-2.5-flash-lite fails
            _fake_grounding_response(),  # gemma succeeds
        ]

        with (
            patch.dict(os.environ, {"KINDLY_GEMINI_API_KEY": "test-key"}, clear=False),
            patch(
                "kindly_web_search_mcp_server.search.gemini_search_tool.get_gemini_client",
                return_value=client,
            ),
        ):
            result = await gemini_search_with_grounding(
                query="test query",
                structured_output=False,
            )

        self.assertEqual(result.model_used, "gemma-4-31b-it")
        self.assertEqual(len(result.fallback_chain), 3)
        # Handle optional fallback_reason
        if result.fallback_reason:
            self.assertIn("service_unavailable", result.fallback_reason)

    async def test_gemini_search_all_tiers_exhausted(self) -> None:
        """Verify error when all tiers exhausted."""
        client = MagicMock()
        exc = _create_mock_error(503)
        client.models.generate_content.side_effect = [exc, exc, exc]

        with (
            patch.dict(os.environ, {"KINDLY_GEMINI_API_KEY": "test-key"}, clear=False),
            patch(
                "kindly_web_search_mcp_server.search.gemini_search_tool.get_gemini_client",
                return_value=client,
            ),
        ):
            result = await gemini_search_with_grounding(
                query="test query",
                structured_output=False,
            )

        self.assertIsNotNone(result.error)
        self.assertIn("All fallback models failed", result.error or "")
        self.assertEqual(len(result.fallback_chain), 3)


if __name__ == "__main__":
    unittest.main()