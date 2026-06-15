from __future__ import annotations

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


class TestLLMReranker(unittest.IsolatedAsyncioTestCase):
    async def test_llm_reranker_uses_gpt_oss_worker_ladder(self) -> None:
        from kindly_web_search_mcp_server.rerank.llm_rerank import rerank_with_llm

        candidates = _build_candidates(2)

        fake_worker = SimpleNamespace(
            complete_structured=AsyncMock(
                return_value=SimpleNamespace(
                    endpoint_name="cerebras",
                    model_name="cerebras/openai/gpt-oss-120b",
                    content='{"ranked_candidate_ids":[2,1]}',
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
        request = fake_worker.complete_structured.await_args.args[0]
        self.assertEqual(request.task, "rerank")
        self.assertEqual(request.response_model.__name__, "RerankLLMOutput")
        langfuse = request.langfuse
        self.assertIsNotNone(langfuse)
        self.assertEqual(langfuse.trace_name, "llm_rerank")
        self.assertEqual(langfuse.metadata["task"], "rerank")
        self.assertEqual(langfuse.metadata["candidate_count"], 2)
        self.assertEqual(langfuse.metadata["top_k"], 2)
        self.assertEqual(
            request.messages[0]["role"],
            "system",
        )
        self.assertIn(
            "Rank passages by relevance",
            request.messages[0]["content"],
        )
        prompt = request.messages[1]["content"]
        self.assertIn("[querystart] find docs [queryend]", prompt)
        self.assertIn("[1]", prompt)
        self.assertIn("[2]", prompt)
        self.assertIn("ranked_candidate_ids", prompt)

    async def test_llm_reranker_caps_the_candidate_window_at_twenty(self) -> None:
        from kindly_web_search_mcp_server.rerank.llm_rerank import rerank_with_llm

        candidates = _build_candidates(25)

        fake_worker = SimpleNamespace(
            complete_structured=AsyncMock(
                return_value=SimpleNamespace(
                    endpoint_name="cerebras",
                    model_name="cerebras/openai/gpt-oss-120b",
                    content='{"ranked_candidate_ids":[20,19,18]}',
                )
            )
        )

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

        request = fake_worker.complete_structured.await_args.args[0]
        prompt = request.messages[1]["content"]
        self.assertEqual(request.langfuse.metadata["candidate_count"], 20)
        self.assertEqual(len(re.findall(r"(?m)^\[\d+\]", prompt)), 20)
        self.assertIn("[20]", prompt)
        self.assertNotIn("[21]", prompt)
        self.assertEqual([item.index for item in outcome.ranked[:3]], [19, 18, 17])
