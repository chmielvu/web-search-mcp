from __future__ import annotations

import logging
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.models import WebSearchResult


def _build_candidates(count: int) -> list[WebSearchResult]:
    return [
        WebSearchResult(
            title=f"Result {index}",
            link=f"https://example.com/{index}",
            snippet=f"snippet {index}",
            score=float(index) / 100.0,
        )
        for index in range(1, count + 1)
    ]


def _make_fake_worker(
    content: str, endpoint: str = "cerebras", model: str = "cerebras/openai/gpt-oss-120b"
):
    """Build a fake LLMWorker that mocks complete_text_messages."""
    return SimpleNamespace(
        complete_text_messages=AsyncMock(
            return_value=SimpleNamespace(
                endpoint_name=endpoint,
                model_name=model,
                content=content,
            )
        )
    )


class TestLLMReranker(unittest.IsolatedAsyncioTestCase):
    async def test_llm_reranker_uses_gpt_oss_worker_ladder(self) -> None:
        from kindly_web_search_mcp_server.rerank.llm_rerank import (
            _build_candidate_window,
            rerank_with_llm,
        )

        candidates = _build_candidates(2)
        window = _build_candidate_window(candidates, 2)
        first_display, first_original, _ = window[0]
        second_display, second_original, _ = window[1]
        fake_worker = _make_fake_worker(
            f"<final_ranking>[{second_display}] > [{first_display}]</final_ranking>"
        )

        with patch(
            "kindly_web_search_mcp_server.rerank.llm_rerank.build_llm_worker",
            return_value=fake_worker,
        ) as mock_worker_factory:
            outcome = await rerank_with_llm(
                query="find docs",
                candidates=candidates,
                top_k=2,
                query_type_hint="general",
                research_goal="Locate canonical docs",
            )

        self.assertEqual([item.index for item in outcome.ranked], [second_original, first_original])
        self.assertEqual([item.score for item in outcome.ranked], [1.0, 0.0])
        self.assertEqual(outcome.endpoint_name, "cerebras")
        self.assertEqual(outcome.model, "cerebras/openai/gpt-oss-120b")
        mock_worker_factory.assert_called_once()
        call_kwargs = fake_worker.complete_text_messages.await_args.kwargs
        self.assertEqual(call_kwargs["task"], "rerank")
        langfuse = call_kwargs["langfuse"]
        self.assertIsNotNone(langfuse)
        self.assertEqual(langfuse.trace_name, "llm_rerank")
        self.assertEqual(langfuse.metadata["task"], "rerank")
        self.assertEqual(langfuse.metadata["candidate_count"], 2)
        self.assertEqual(langfuse.metadata["top_k"], 2)
        messages = call_kwargs["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Information Retrieval Judge", messages[0]["content"])
        self.assertIn("untrusted search-result data", messages[0]["content"])
        prompt = messages[1]["content"]
        self.assertIn("<query>find docs</query>", prompt)
        self.assertIn("<final_ranking>", prompt)
        self.assertIn('<candidate_data type="untrusted_search_result">', prompt)

    async def test_llm_reranker_caps_the_candidate_window_at_twenty(self) -> None:
        from kindly_web_search_mcp_server.rerank.llm_rerank import (
            _build_candidate_window,
            rerank_with_llm,
        )

        candidates = _build_candidates(25)
        window = _build_candidate_window(candidates, 20)
        fake_worker = _make_fake_worker("<final_ranking>[20] > [19] > [18]</final_ranking>")

        with patch(
            "kindly_web_search_mcp_server.rerank.llm_rerank.build_llm_worker",
            return_value=fake_worker,
        ):
            outcome = await rerank_with_llm(
                query="find docs",
                candidates=candidates,
                top_k=10,
                candidate_limit=20,
                query_type_hint="general",
                research_goal="Locate canonical docs",
            )

        call_kwargs = fake_worker.complete_text_messages.await_args.kwargs
        prompt = call_kwargs["messages"][1]["content"]
        self.assertEqual(call_kwargs["langfuse"].metadata["candidate_count"], 20)
        self.assertEqual(len(re.findall(r"(?m)^\[\d+\]", prompt)), 20)
        self.assertIn("[20]", prompt)
        self.assertNotIn("[21]", prompt)
        display_to_original = {display: original for display, original, _ in window}
        self.assertEqual(
            [item.index for item in outcome.ranked[:3]],
            [display_to_original[20], display_to_original[19], display_to_original[18]],
        )
        self.assertAlmostEqual(outcome.ranked[0].score, 1.0)
        self.assertAlmostEqual(outcome.ranked[1].score, 18 / 19)
        self.assertAlmostEqual(outcome.ranked[2].score, 17 / 19)

    async def test_llm_reranker_parses_only_final_ranking_and_skips_hallucinated_ids(self) -> None:
        from kindly_web_search_mcp_server.rerank.llm_rerank import (
            _build_candidate_window,
            rerank_with_llm,
        )

        candidates = _build_candidates(10)
        window = _build_candidate_window(candidates, 10)
        fake_worker = _make_fake_worker(
            "<evaluation>Candidate [1] is relevant.</evaluation>"
            "<final_ranking>[135246] > [3] > [5] > [99999] > [2] > [1]</final_ranking>",
            endpoint="groq",
            model="groq/gpt-oss-120b",
        )

        with patch(
            "kindly_web_search_mcp_server.rerank.llm_rerank.build_llm_worker",
            return_value=fake_worker,
        ):
            with self.assertLogs(
                "kindly_web_search_mcp_server.rerank.llm_rerank", level=logging.WARNING
            ) as captured:
                outcome = await rerank_with_llm(
                    query="find docs",
                    candidates=candidates,
                    top_k=5,
                )

        display_to_original = {display: original for display, original, _ in window}
        self.assertEqual(
            [item.index for item in outcome.ranked[:4]],
            [
                display_to_original[3],
                display_to_original[5],
                display_to_original[2],
                display_to_original[1],
            ],
        )
        warning_msgs = [r.getMessage() for r in captured.records]
        self.assertEqual(len(warning_msgs), 2)
        self.assertIn("135246", warning_msgs[0])
        self.assertIn("99999", warning_msgs[1])
