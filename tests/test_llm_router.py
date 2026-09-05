from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestLLMRouter(unittest.IsolatedAsyncioTestCase):
    def test_worker_router_chain_has_correct_providers(self) -> None:
        """`build_worker_router()` produces an LLMRouter with Groq keys first."""
        from kindly_web_search_mcp_server.inference.router import build_worker_router
        from kindly_web_search_mcp_server.inference import catalog
        from kindly_web_search_mcp_server.settings import settings

        try:
            with (
                patch.object(settings, "groq_rewrite_model", "groq-test"),
                patch.object(settings, "huggingface_rewrite_model", "hf-test"),
                patch.object(settings, "vercel_rewrite_model", "vercel-test"),
            ):
                catalog._register_all()
                router = build_worker_router()
                models = [(m.provider, m.model_id, m.api_key_env) for m in router.chain.models]
        finally:
            catalog._register_all()

        self.assertEqual(len(models), 4)
        self.assertEqual(
            models,
            [
                ("groq", "groq-test", "GROQ_API_KEY"),
                ("groq", "groq-test", "SECOND_GROQ_API_KEY"),
                ("huggingface", "hf-test", "HF_TOKEN"),
                ("vercel", "vercel-test", "AI_GATEWAY_API_KEY"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
