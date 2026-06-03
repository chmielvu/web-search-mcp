"""TDD tests for Phase 7.2: candidate injection from result memory into orchestrator merge.

Per plan:
- memory candidates -> virtual provider list provider="result_memory"
- list weight from settings.result_memory_candidate_weight
- dedup by URL via existing merge_search_results behavior
- convert to WebSearchResult with resource_type="cached"
- store survivors after rerank + emit candidate_survived
- run combined: test_result_memory_injection.py + test_search_orchestrator.py + test_rerank_core.py

Exact command:
. .\.venv\Scripts\Activate.ps1 ; python -m pytest tests/test_result_memory_injection.py -q
then combined.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestResultMemoryCandidateInjection(unittest.TestCase):
    def test_memory_candidates_injected_as_virtual_list_before_rrf(self) -> None:
        from kindly_web_search_mcp_server.search.orchestrator import run_web_search
        from kindly_web_search_mcp_server.settings import settings

        # sample memory candidates as returned by store (dict shape)
        mem_cands = [
            {"url": "https://mem.com/1", "title": "Mem Result", "snippet": "from past", "source_query": "past q"},
            {"url": "https://fresh.com/a", "title": "Fresh overlap", "snippet": "also in providers"},
        ]

        async def _run() -> None:
            with (
                patch(
                    "kindly_web_search_mcp_server.search.orchestrator.rewrite_search_query",
                    new_callable=AsyncMock,
                ) as mock_rewrite,
                patch(
                    "kindly_web_search_mcp_server.search.orchestrator.search_single_query",
                    new_callable=AsyncMock,
                ) as mock_single,
                patch(
                    "kindly_web_search_mcp_server.search.orchestrator.merge_search_results",
                ) as mock_merge,
                patch(
                    "kindly_web_search_mcp_server.search.orchestrator.get_result_memory_store",
                ) as mock_get_mem,
                patch(
                    "kindly_web_search_mcp_server.search.orchestrator.embed_query",
                    new_callable=AsyncMock,
                ) as mock_embed,
            ):
                # setup rewrite bypass for simplicity
                mock_rewrite.return_value = None  # causes single query path
                mock_single.return_value = [
                    type("R", (), {"title": "Fresh 1", "link": "https://fresh.com/a", "snippet": "live", "domain": "fresh.com", "providers": ["searxng"], "resource_type": None})()
                ]
                mock_embed.return_value = [0.1] * 384
                mock_mem = mock_get_mem.return_value
                mock_mem.lookup_candidates.return_value = mem_cands

                # call (will use real merge unless patched, but we patch merge to inspect call)
                # to let real merge run for dedup test, unpatch? but use side
                mock_merge.return_value = []  # capture later

                # invoke
                # since rewrite=None path
                await run_web_search("test mem injection", num_results=3, rewrite=False)

                # assert called with extra list + weight
                self.assertTrue(mock_get_mem.called)
                self.assertTrue(mock_mem.lookup_candidates.called)
                self.assertTrue(mock_embed.called)

                # the call to merge should have had 2 lists: the provider one + memory one
                call_kwargs = mock_merge.call_args.kwargs if mock_merge.call_args else {}
                result_lists = call_kwargs.get("result_lists") or (mock_merge.call_args[0][0] if mock_merge.call_args else [])
                list_weights = call_kwargs.get("list_weights")

                self.assertEqual(len(result_lists), 2, "memory list should be appended")
                # second list is the memory virtual
                mem_list = result_lists[1]
                self.assertEqual(len(mem_list), 2)
                self.assertEqual(mem_list[0].providers, ["result_memory"])
                self.assertEqual(mem_list[0].resource_type, "cached")
                self.assertEqual(mem_list[0].link, "https://mem.com/1")

                self.assertIsNotNone(list_weights)
                self.assertAlmostEqual(list_weights[1], settings.result_memory_candidate_weight)

        asyncio.run(_run())

    def test_memory_candidates_deduped_by_url_in_merge(self) -> None:
        # relies on merge_search_results existing dedup + _pick_better preferring higher weight list? but memory has low weight
        from kindly_web_search_mcp_server.search.merge import merge_search_results
        from kindly_web_search_mcp_server.models import WebSearchResult

        fresh = WebSearchResult(title="F", link="https://dup.com", snippet="live", providers=["searxng"])
        memc = WebSearchResult(title="M", link="https://dup.com", snippet="old cached", providers=["result_memory"], resource_type="cached")

        merged = merge_search_results(
            [[fresh], [memc]],
            list_weights=[1.0, 0.5],
        )
        self.assertEqual(len(merged), 1)
        self.assertIn("searxng", merged[0].providers)  # the fresh one kept (higher weight)

    def test_survivors_after_rerank_emit_candidate_survived(self) -> None:
        from kindly_web_search_mcp_server.search.orchestrator import run_web_search

        mem_cands = [{"url": "https://survive.com", "title": "S", "snippet": "old"}]

        async def _run() -> None:
            captured_events = []

            def capture_emit(logger, event, **f):
                if event.startswith("result_memory."):
                    captured_events.append((event, f))

            with (
                patch("kindly_web_search_mcp_server.search.orchestrator.rewrite_search_query", new_callable=AsyncMock) as mr,
                patch("kindly_web_search_mcp_server.search.orchestrator.search_single_query", new_callable=AsyncMock) as ms,
                patch("kindly_web_search_mcp_server.search.orchestrator.embed_query", new_callable=AsyncMock) as me,
                patch("kindly_web_search_mcp_server.search.orchestrator.get_result_memory_store") as mg2,
                patch("kindly_web_search_mcp_server.search.orchestrator.emit_observability_event", side_effect=capture_emit),
                patch("kindly_web_search_mcp_server.search.orchestrator._rerank_results", new_callable=AsyncMock) as mrer,
            ):
                mr.return_value = None
                ms.return_value = []
                me.return_value = [0.01]*384
                mg2.return_value.lookup_candidates.return_value = mem_cands
                # after rerank keep the mem one
                async def fake_rerank(q, cands, **k):
                    return cands  # keep injected
                mrer.side_effect = fake_rerank

                await run_web_search("survivor test", num_results=1, rewrite=False)

                survived_events = [(e, f) for e, f in captured_events if e == "result_memory.candidate_survived"]
                self.assertTrue(len(survived_events) >= 1, "should emit candidate_survived when mem cand survives rerank")
                ev = survived_events[0][1]
                self.assertIn("survived_count", ev)
                self.assertGreaterEqual(ev.get("survived_count", 0), 1)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
