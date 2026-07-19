"""Tests for quick_web_search via Parallel AI Search API."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _mock_search_result(
    *, results=None, search_id="search_abc", session_id="session_xyz", warnings=None, usage=None
):
    mock = MagicMock()
    mock.results = results or []
    mock.search_id = search_id
    mock.session_id = session_id
    mock.warnings = warnings
    mock.usage = usage
    return mock


def _mock_web_result(url, title=None, publish_date=None, excerpts=None):
    mock = MagicMock()
    mock.url = url
    mock.title = title
    mock.publish_date = publish_date
    mock.excerpts = excerpts or []
    return mock


class TestQuickWebSearch(unittest.TestCase):
    def test_happy_path_maps_citations_and_metadata(self) -> None:
        async def run() -> None:
            from kindly_web_search_mcp_server.quick_web_search import _quick_web_search_impl

            search_result = _mock_search_result(
                results=[
                    _mock_web_result(
                        url="https://example.com",
                        title="Example",
                        publish_date="2026-06-01",
                        excerpts=["First excerpt", "Second excerpt"],
                    ),
                    _mock_web_result(
                        url="https://test.com",
                        title="Test Site",
                        publish_date=None,
                        excerpts=["Solo excerpt"],
                    ),
                ],
                search_id="search_123",
                session_id="session_456",
            )

            mock_client = MagicMock()
            mock_client.search = AsyncMock(return_value=search_result)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            with (
                patch(
                    "kindly_web_search_mcp_server.quick_web_search.AsyncParallel",
                    return_value=mock_client,
                ),
                patch("kindly_web_search_mcp_server.quick_web_search.settings") as mock_settings,
            ):
                mock_settings.parallel_api_key = "test-key"
                response = await _quick_web_search_impl(
                    ["test query"], objective="Find information about testing"
                )

            self.assertEqual(response.search_queries, ["test query"])
            self.assertEqual(response.total_citations, 2)
            self.assertEqual(response.search_id, "search_123")
            self.assertEqual(response.session_id, "session_456")
            self.assertIsNone(response.warnings)
            self.assertIsNone(response.usage)

            self.assertEqual(response.citations[0].title, "Example")
            self.assertEqual(response.citations[0].url, "https://example.com")
            self.assertEqual(response.citations[0].publish_date, "2026-06-01")
            self.assertEqual(response.citations[0].snippet, "First excerpt\nSecond excerpt")
            self.assertEqual(response.citations[0].excerpts, ["First excerpt", "Second excerpt"])

            self.assertEqual(response.citations[1].snippet, "Solo excerpt")
            self.assertIsNone(response.citations[1].publish_date)

        anyio.run(run)

    def test_passes_multi_query_and_all_options_to_client(self) -> None:
        async def run() -> None:
            from kindly_web_search_mcp_server.quick_web_search import _quick_web_search_impl

            search_result = _mock_search_result()
            mock_client = MagicMock()
            mock_client.search = AsyncMock(return_value=search_result)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            with (
                patch(
                    "kindly_web_search_mcp_server.quick_web_search.AsyncParallel",
                    return_value=mock_client,
                ),
                patch("kindly_web_search_mcp_server.quick_web_search.settings") as mock_settings,
            ):
                mock_settings.parallel_api_key = "test-key"
                await _quick_web_search_impl(
                    ["query one", "query two", "query three"],
                    objective="Research goal here",
                    max_results=5,
                    max_chars_total=20000,
                    max_chars_per_result=8000,
                    client_model="claude-opus-4-7",
                    session_id="my-session",
                    include_domains=["example.com"],
                    exclude_domains=["x.com"],
                    after_date="2026-01-01",
                    location="us",
                    max_age_seconds=3600,
                    timeout_seconds=30.0,
                    disable_cache_fallback=True,
                )

            call_kwargs = mock_client.search.await_args.kwargs
            self.assertEqual(
                call_kwargs["search_queries"], ["query one", "query two", "query three"]
            )
            self.assertEqual(call_kwargs["objective"], "Research goal here")
            self.assertEqual(call_kwargs["mode"], "advanced")
            self.assertEqual(call_kwargs["max_chars_total"], 20000)
            self.assertEqual(call_kwargs["client_model"], "claude-opus-4-7")
            self.assertEqual(call_kwargs["session_id"], "my-session")

            adv = call_kwargs["advanced_settings"]
            self.assertEqual(adv["max_results"], 5)
            self.assertEqual(adv["source_policy"]["include_domains"], ["example.com"])
            self.assertEqual(adv["source_policy"]["exclude_domains"], ["x.com"])
            self.assertEqual(adv["source_policy"]["after_date"], "2026-01-01")
            self.assertEqual(adv["location"], "us")
            self.assertEqual(adv["excerpt_settings"]["max_chars_per_result"], 8000)
            self.assertEqual(adv["fetch_policy"]["max_age_seconds"], 3600)
            self.assertEqual(adv["fetch_policy"]["timeout_seconds"], 30.0)
            self.assertTrue(adv["fetch_policy"]["disable_cache_fallback"])

        anyio.run(run)

    def test_empty_results_returns_zero_citations(self) -> None:
        async def run() -> None:
            from kindly_web_search_mcp_server.quick_web_search import _quick_web_search_impl

            search_result = _mock_search_result(
                results=[],
                search_id="empty",
                session_id="empty",
            )
            mock_client = MagicMock()
            mock_client.search = AsyncMock(return_value=search_result)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            with (
                patch(
                    "kindly_web_search_mcp_server.quick_web_search.AsyncParallel",
                    return_value=mock_client,
                ),
                patch("kindly_web_search_mcp_server.quick_web_search.settings") as mock_settings,
            ):
                mock_settings.parallel_api_key = "test-key"
                response = await _quick_web_search_impl(["test"], objective="Find nothing")

            self.assertEqual(response.total_citations, 0)
            self.assertEqual(response.citations, [])

        anyio.run(run)

    def test_api_error_raises_runtime_error(self) -> None:
        async def run() -> None:
            from kindly_web_search_mcp_server.quick_web_search import _quick_web_search_impl

            mock_client = MagicMock()
            mock_client.search = AsyncMock(side_effect=RuntimeError("API down"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            with (
                patch(
                    "kindly_web_search_mcp_server.quick_web_search.AsyncParallel",
                    return_value=mock_client,
                ),
                patch("kindly_web_search_mcp_server.quick_web_search.settings") as mock_settings,
            ):
                mock_settings.parallel_api_key = "test-key"
                with self.assertRaises(RuntimeError) as ctx:
                    await _quick_web_search_impl(["test"], objective="Will fail")
                self.assertIn("Parallel search failed", str(ctx.exception))
                self.assertIn("API down", str(ctx.exception))

        anyio.run(run)

    def test_missing_api_key_raises_runtime_error(self) -> None:
        async def run() -> None:
            from kindly_web_search_mcp_server.quick_web_search import _quick_web_search_impl

            with patch("kindly_web_search_mcp_server.quick_web_search.settings") as mock_settings:
                mock_settings.parallel_api_key = ""
                with self.assertRaises(RuntimeError) as ctx:
                    await _quick_web_search_impl(["test"], objective="no key")
                self.assertIn("PARALLEL_API_KEY is not set", str(ctx.exception))

        anyio.run(run)

    def test_maps_warnings_and_usage_when_present(self) -> None:
        async def run() -> None:
            from kindly_web_search_mcp_server.quick_web_search import _quick_web_search_impl
            from parallel.types import Warning as PWarning, UsageItem

            warn = PWarning(
                type="input_validation_warning", message="location ignored", detail=None
            )
            usage = UsageItem(name="sku_search_basic", count=1)

            search_result = _mock_search_result(
                results=[_mock_web_result(url="https://a.com", excerpts=["hello"])],
                warnings=[warn],
                usage=[usage],
            )
            mock_client = MagicMock()
            mock_client.search = AsyncMock(return_value=search_result)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            with (
                patch(
                    "kindly_web_search_mcp_server.quick_web_search.AsyncParallel",
                    return_value=mock_client,
                ),
                patch("kindly_web_search_mcp_server.quick_web_search.settings") as mock_settings,
            ):
                mock_settings.parallel_api_key = "test-key"
                response = await _quick_web_search_impl(["test"], objective="warny")

            self.assertEqual(len(response.warnings), 1)
            self.assertEqual(response.warnings[0]["type"], "input_validation_warning")
            self.assertEqual(len(response.usage), 1)
            self.assertEqual(response.usage[0]["name"], "sku_search_basic")
            self.assertEqual(response.usage[0]["count"], 1)

        anyio.run(run)

    # ── Validation tests ─────────────────────────────────────────────

    def test_empty_search_queries_raises_value_error(self) -> None:
        async def run() -> None:
            from kindly_web_search_mcp_server.quick_web_search import _quick_web_search_impl

            with patch("kindly_web_search_mcp_server.quick_web_search.settings") as mock_settings:
                mock_settings.parallel_api_key = "test-key"
                with self.assertRaises(ValueError) as ctx:
                    await _quick_web_search_impl([], objective="empty list")
                self.assertIn("search_queries must contain at least 1", str(ctx.exception))

        anyio.run(run)

    def test_too_many_search_queries_raises_value_error(self) -> None:
        async def run() -> None:
            from kindly_web_search_mcp_server.quick_web_search import _quick_web_search_impl

            with patch("kindly_web_search_mcp_server.quick_web_search.settings") as mock_settings:
                mock_settings.parallel_api_key = "test-key"
                with self.assertRaises(ValueError) as ctx:
                    await _quick_web_search_impl(
                        ["a", "b", "c", "d", "e", "f"], objective="too many"
                    )
                self.assertIn("must not exceed 5", str(ctx.exception))

        anyio.run(run)

    def test_blank_search_query_member_raises_value_error(self) -> None:
        async def run() -> None:
            from kindly_web_search_mcp_server.quick_web_search import _quick_web_search_impl

            with patch("kindly_web_search_mcp_server.quick_web_search.settings") as mock_settings:
                mock_settings.parallel_api_key = "test-key"
                with self.assertRaises(ValueError) as ctx:
                    await _quick_web_search_impl(["good one", "  "], objective="blank query")
                self.assertIn("must not contain blank strings", str(ctx.exception))

        anyio.run(run)

    def test_max_age_seconds_below_600_raises_value_error(self) -> None:
        async def run() -> None:
            from kindly_web_search_mcp_server.quick_web_search import _quick_web_search_impl

            with patch("kindly_web_search_mcp_server.quick_web_search.settings") as mock_settings:
                mock_settings.parallel_api_key = "test-key"
                with self.assertRaises(ValueError) as ctx:
                    await _quick_web_search_impl(["test"], objective="bad age", max_age_seconds=599)
                self.assertIn("max_age_seconds must be >= 600", str(ctx.exception))

        anyio.run(run)


if __name__ == "__main__":
    unittest.main()
