from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.models import WebSearchResult


class TestLLMReranker(unittest.IsolatedAsyncioTestCase):
    async def test_llm_reranker_uses_gpt_oss_worker_ladder(self) -> None:
        from kindly_web_search_mcp_server.rerank.llm_rerank import rerank_with_llm

        candidates = [
            WebSearchResult(
                title="A",
                link="https://example.com/a",
                snippet="snippet a",
                score=0.1,
            ),
            WebSearchResult(
                title="B",
                link="https://example.com/b",
                snippet="snippet b",
                score=0.2,
            ),
        ]

        fake_worker = SimpleNamespace(
            complete_text_messages=AsyncMock(
                return_value=SimpleNamespace(
                    endpoint_name="cerebras",
                    model_name="cerebras/openai/gpt-oss-120b",
                    content="[rankstart] [2] > [1] [rankend]",
                )
            )
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

        self.assertEqual([item.index for item in outcome.ranked], [1, 0])
        self.assertEqual(outcome.endpoint_name, "cerebras")
        self.assertEqual(outcome.model, "cerebras/openai/gpt-oss-120b")
        mock_worker_factory.assert_called_once()
        self.assertEqual(
            fake_worker.complete_text_messages.await_args.kwargs["task"],
            "rerank",
        )
        langfuse = fake_worker.complete_text_messages.await_args.kwargs["langfuse"]
        self.assertIsNotNone(langfuse)
        self.assertEqual(langfuse.trace_name, "llm_rerank")
        self.assertEqual(langfuse.metadata["task"], "rerank")
        self.assertEqual(langfuse.metadata["candidate_count"], 2)
        self.assertEqual(langfuse.metadata["top_k"], 2)
        self.assertEqual(
            fake_worker.complete_text_messages.await_args.kwargs["messages"][0]["role"],
            "system",
        )
        self.assertIn(
            "Rank passages by relevance",
            fake_worker.complete_text_messages.await_args.kwargs["messages"][0]["content"],
        )
        prompt = fake_worker.complete_text_messages.await_args.kwargs["messages"][1]["content"]
        self.assertIn("[querystart] find docs [queryend]", prompt)
        self.assertIn("[1]", prompt)
        self.assertIn("[2]", prompt)
        self.assertIn("[rankstart]", prompt)
        self.assertIn("[rankend]", prompt)
