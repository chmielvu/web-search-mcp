from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestRerankStack(unittest.TestCase):
    def test_bi_cross_stack_plan(self) -> None:
        from kindly_web_search_mcp_server.rerank.stack import build_rerank_stack_plan

        plan = build_rerank_stack_plan("bi_cross")

        self.assertEqual(plan.mode, "bi_cross")
        self.assertTrue(plan.use_cross_encoder)
        self.assertFalse(plan.use_llm_reranker)
        self.assertEqual(
            plan.stage_order,
            ("bi_encoder", "cross_encoder", "diversity"),
        )

    def test_bi_llm_stack_plan(self) -> None:
        from kindly_web_search_mcp_server.rerank.stack import build_rerank_stack_plan

        plan = build_rerank_stack_plan("bi_llm")

        self.assertEqual(plan.mode, "bi_llm")
        self.assertFalse(plan.use_cross_encoder)
        self.assertTrue(plan.use_llm_reranker)
        self.assertEqual(
            plan.stage_order,
            ("bi_encoder", "llm_reranker", "diversity"),
        )

    def test_bi_cross_llm_stack_plan(self) -> None:
        from kindly_web_search_mcp_server.rerank.stack import build_rerank_stack_plan

        plan = build_rerank_stack_plan("bi_cross_llm")

        self.assertEqual(plan.mode, "bi_cross_llm")
        self.assertTrue(plan.use_cross_encoder)
        self.assertTrue(plan.use_llm_reranker)
        self.assertEqual(
            plan.stage_order,
            ("bi_encoder", "cross_encoder", "llm_reranker", "diversity"),
        )

    def test_invalid_stack_mode_raises(self) -> None:
        from kindly_web_search_mcp_server.rerank.stack import build_rerank_stack_plan

        with self.assertRaises(ValueError):
            build_rerank_stack_plan("bi_cross_encoder")
