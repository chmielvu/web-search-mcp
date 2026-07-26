from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestRerankSettings(unittest.TestCase):
    def test_rankllm_defaults(self) -> None:
        from kindly_web_search_mcp_server.settings import Settings

        configured = Settings()
        self.assertEqual(configured.rankllm_openrouter_model, "nvidia/nemotron-3-nano-30b-a3b:free")
        self.assertEqual(configured.rankllm_gemini_model, "gemini-3.5-flash-lite")
        self.assertEqual(configured.rankllm_timeout_seconds, 20.0)

    def test_rerank_bi_encoder_defaults(self) -> None:
        from kindly_web_search_mcp_server.settings import Settings

        configured = Settings()
        self.assertEqual(configured.rerank_bi_encoder_timeout_seconds, 15.0)
        self.assertEqual(configured.rerank_bi_encoder_text_max_chars, 384)
        self.assertEqual(configured.rerank_bi_encoder_batch_size, 64)
        self.assertEqual(configured.rerank_bi_encoder_max_concurrent_batches, 3)
