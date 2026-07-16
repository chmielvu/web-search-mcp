from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.models import WebSearchResult


def _candidate(index: int) -> WebSearchResult:
    return WebSearchResult(
        title=f"Candidate {index}",
        link=f"https://example.com/{index}",
        snippet=("long snippet " * 80) + str(index),
    )


class TestBiEncoderFilter(unittest.IsolatedAsyncioTestCase):
    async def test_candidate_embedding_texts_are_bounded_before_hf_call(self) -> None:
        from kindly_web_search_mcp_server.rerank.bi_encoder import bi_encoder_filter

        captured_texts: list[str] = []

        async def fake_embed_texts(texts: list[str], **_: object) -> list[list[float]]:
            captured_texts.extend(texts)
            return [[1.0, 0.0, 0.0] for _ in texts]

        with (
            patch("kindly_web_search_mcp_server.rerank.bi_encoder.settings") as s,
            patch(
                "kindly_web_search_mcp_server.rerank.bi_encoder.embed_texts",
                new_callable=AsyncMock,
            ) as mock_embed,
        ):
            s.rerank_bi_encoder_text_max_chars = 96
            s.rerank_bi_encoder_batch_size = 8
            s.rerank_bi_encoder_max_concurrent_batches = 3
            s.rerank_bi_encoder_timeout_seconds = 8.0
            mock_embed.side_effect = fake_embed_texts

            candidates = [_candidate(i) for i in range(6)]
            await bi_encoder_filter([1.0, 0.0, 0.0], candidates, top_k=3)

        self.assertEqual(len(captured_texts), 6)
        self.assertTrue(all(len(text) <= 96 for text in captured_texts))
        self.assertTrue(all(text.startswith("Candidate ") for text in captured_texts))

    async def test_candidate_embeddings_are_sent_in_bounded_batches(self) -> None:
        from kindly_web_search_mcp_server.rerank.bi_encoder import bi_encoder_filter

        batch_sizes: list[int] = []

        async def fake_embed_texts(texts: list[str], **_: object) -> list[list[float]]:
            batch_sizes.append(len(texts))
            return [[1.0, 0.0, 0.0] for _ in texts]

        with (
            patch("kindly_web_search_mcp_server.rerank.bi_encoder.settings") as s,
            patch(
                "kindly_web_search_mcp_server.rerank.bi_encoder.embed_texts",
                new_callable=AsyncMock,
            ) as mock_embed,
        ):
            s.rerank_bi_encoder_text_max_chars = 384
            s.rerank_bi_encoder_batch_size = 4
            s.rerank_bi_encoder_max_concurrent_batches = 2
            s.rerank_bi_encoder_timeout_seconds = 8.0
            mock_embed.side_effect = fake_embed_texts

            candidates = [_candidate(i) for i in range(10)]
            result, context = await bi_encoder_filter([1.0, 0.0, 0.0], candidates, top_k=5)

        self.assertEqual(batch_sizes, [4, 4, 2])
        self.assertEqual(len(result), 5)
        self.assertIsNotNone(context)
        self.assertEqual(len(context.candidates), 10)


class TestConditionalBiEncoder(unittest.IsolatedAsyncioTestCase):
    async def test_exactly_one_hundred_skips_bi_encoder(self) -> None:
        from kindly_web_search_mcp_server.rerank import conditional_bi

        candidates = [_candidate(index) for index in range(100)]
        with (
            patch.object(conditional_bi, "embed_query", new_callable=AsyncMock) as embed_query,
            patch.object(conditional_bi, "bi_encoder_rank", new_callable=AsyncMock) as rank,
        ):
            outcome = await conditional_bi.run_conditional_bi_encoder(
                "query",
                candidates,
                precomputed_embedding=None,
                logger=conditional_bi.logging.getLogger("test"),
            )
        embed_query.assert_not_awaited()
        rank.assert_not_awaited()
        self.assertEqual(outcome.candidates, candidates)
        self.assertEqual(outcome.status, "candidate_count_not_above_cross_limit")

    async def test_one_hundred_one_ranks_full_pool_and_retains_head(self) -> None:
        from kindly_web_search_mcp_server.rerank import conditional_bi

        candidates = [_candidate(index) for index in range(101)]
        ranked = list(reversed(candidates))
        context = object()
        with (
            patch.object(
                conditional_bi,
                "embed_query",
                new=AsyncMock(return_value=[1.0, 0.0]),
            ),
            patch.object(
                conditional_bi,
                "bi_encoder_rank",
                new=AsyncMock(return_value=(ranked, context)),
            ) as rank,
        ):
            outcome = await conditional_bi.run_conditional_bi_encoder(
                "query",
                candidates,
                precomputed_embedding=None,
                logger=conditional_bi.logging.getLogger("test"),
            )
        rank.assert_awaited_once()
        self.assertEqual(outcome.candidates, ranked[:100])
        self.assertIs(outcome.embedding_context, context)
        self.assertEqual(outcome.status, "applied")

    async def test_embedding_failure_retains_incoming_head(self) -> None:
        from kindly_web_search_mcp_server.rerank import conditional_bi

        candidates = [_candidate(index) for index in range(101)]
        with (
            patch.object(
                conditional_bi,
                "embed_query",
                new=AsyncMock(side_effect=TimeoutError("timeout")),
            ),
        ):
            outcome = await conditional_bi.run_conditional_bi_encoder(
                "query",
                candidates,
                precomputed_embedding=None,
                logger=conditional_bi.logging.getLogger("test"),
            )
        self.assertEqual(outcome.candidates, candidates[:100])
        self.assertEqual(outcome.status, "query_embedding_failure")


if __name__ == "__main__":
    unittest.main()
