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


def _make_fake_worker(content: str, endpoint: str = "cerebras", model: str = "cerebras/openai/gpt-oss-120b"):
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
        from kindly_web_search_mcp_server.rerank.llm_rerank import rerank_with_llm

        candidates = _build_candidates(2)

        fake_worker = _make_fake_worker("[rankstart] [2] > [1] [rankend]")

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

        self.assertEqual([item.index for item in outcome.ranked], [1, 0])
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
        self.assertIn("Rank passages by relevance", messages[0]["content"])
        prompt = messages[1]["content"]
        self.assertIn("[querystart] find docs [queryend]", prompt)
        self.assertIn("[1]", prompt)
        self.assertIn("[2]", prompt)

    async def test_llm_reranker_caps_the_candidate_window_at_twenty(self) -> None:
        from kindly_web_search_mcp_server.rerank.llm_rerank import rerank_with_llm

        candidates = _build_candidates(25)

        fake_worker = _make_fake_worker("[rankstart] [20] > [19] > [18] [rankend]")

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
        self.assertEqual([item.index for item in outcome.ranked[:3]], [19, 18, 17])

    async def test_llm_reranker_skips_hallucinated_out_of_range_ids(self) -> None:
        from kindly_web_search_mcp_server.rerank.llm_rerank import rerank_with_llm

        candidates = _build_candidates(10)

        fake_worker = _make_fake_worker(
            "[rankstart] [135246] > [3] > [5] > [99999] > [2] > [1] [rankend]",
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

        # Valid IDs preserved in order: 3, 5, 2, 1 -> indices 2, 4, 1, 0
        self.assertEqual([item.index for item in outcome.ranked[:4]], [2, 4, 1, 0])
        # Hallucinated IDs 135246 and 99999 were skipped with warnings
        warning_msgs = [r.getMessage() for r in captured.records]
        self.assertEqual(len(warning_msgs), 2)
        self.assertIn("135246", warning_msgs[0])
        self.assertIn("99999", warning_msgs[1])
