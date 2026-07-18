from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.search.contracts import BranchRole, QueryBranch, SearchRun
from kindly_web_search_mcp_server.settings import settings
from kindly_web_search_mcp_server.search.options import SearchOptions


class TestHardBudgetTiming(unittest.IsolatedAsyncioTestCase):
    async def test_retrieve_branches_timeout_cancellation_and_no_retries(self):
        from kindly_web_search_mcp_server.search.retrieval import retrieve_branches

        # Mock settings budget to 1.0 second.
        # With 0.5s grace period, the asyncio.wait timeout will be 0.5s.
        with patch.object(settings, "search_retrieve_budget_seconds", 1.0):
            request = MagicMock()
            request.options = SearchOptions()
            run = SearchRun(
                request=request,
                http_client=AsyncMock(),
                run_key="test-key",
            )
            branch = QueryBranch(
                role=BranchRole.ORIGINAL_FREE,
                query="test query",
                provider_names=("ddg", "gemma"),
                max_results=5,
            )
            plan = MagicMock()
            plan.branches = (branch,)
            plan.provider_arguments = {}
            run.plan = plan

            from kindly_web_search_mcp_server.search.provider_catalog import (
                ProviderDefinition,
            )

            def mock_get_def(name):
                return ProviderDefinition(
                    name=name,
                    adapter_module="ddg",
                    adapter_function="search_ddg",
                    description="mock",
                    default_timeout_seconds=5.0,
                )

            ddg_calls = 0
            gemma_calls = 0

            async def mock_ddg_adapter(*args, **kwargs):
                nonlocal ddg_calls
                ddg_calls += 1
                return [WebSearchResult(title="DDG Hit", link="https://ddg.com", snippet="ddg")]

            async def mock_gemma_adapter(*args, **kwargs):
                nonlocal gemma_calls
                gemma_calls += 1
                # Sleep longer than the wait timeout (0.5s)
                await asyncio.sleep(2.0)
                return [
                    WebSearchResult(title="Gemma Hit", link="https://gemma.com", snippet="gemma")
                ]

            def mock_get_adapter(name):
                if name == "ddg":
                    return mock_ddg_adapter
                return mock_gemma_adapter

            with (
                patch(
                    "kindly_web_search_mcp_server.search.retrieval.get_provider_definition",
                    mock_get_def,
                ),
                patch(
                    "kindly_web_search_mcp_server.search.retrieval.get_provider_adapter",
                    mock_get_adapter,
                ),
            ):
                start_time = time.monotonic()
                outcomes = await retrieve_branches(run, embedding_task=None)
                elapsed = time.monotonic() - start_time

                # The wait timeout is 1.0 - 0.5 = 0.5 seconds.
                # Total elapsed should be around 0.5s.
                self.assertLess(elapsed, 0.9)
                self.assertEqual(len(outcomes), 1)
                outcome = outcomes[0]

                # DDG succeeded, Gemma timed out/was cancelled.
                ddg_hits = [r for r in outcome.results if "ddg" in (r.providers or [])]
                gemma_hits = [r for r in outcome.results if "gemma" in (r.providers or [])]

                self.assertTrue(len(ddg_hits) > 0)
                self.assertEqual(len(gemma_hits), 0)

                # Both should be in attempted list.
                self.assertIn("ddg", outcome.attempted_provider_names)
                self.assertIn("gemma", outcome.attempted_provider_names)

                # Each adapter must be called exactly once (no retries).
                self.assertEqual(ddg_calls, 1)
                self.assertEqual(gemma_calls, 1)
