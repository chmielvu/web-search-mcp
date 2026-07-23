from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestLLMRouter(unittest.IsolatedAsyncioTestCase):
    def test_worker_endpoints_have_canonical_provider_model(self) -> None:
        """`build_worker_endpoints()` produces one LLMEndpoint per provider
        with the correct `name` and `model` fields. The `route_model` field
        that was asserted previously was removed in the 2026-07-20 refactor
        (the route-prefix logic moved to a standalone `_route_model()` helper
        in rerank/llm_rerank.py)."""
        from kindly_web_search_mcp_server.llm.config import build_worker_endpoints
        from kindly_web_search_mcp_server.settings import settings

        with (
            patch.object(settings, "cerebras_rewrite_model", "cerebras-test"),
            patch.object(settings, "groq_rewrite_model", "groq-test"),
            patch.object(settings, "huggingface_rewrite_model", "hf-test"),
            patch.object(settings, "vercel_rewrite_model", "vercel-test"),
        ):
            endpoints = build_worker_endpoints()

        self.assertEqual(len(endpoints), 4)
        self.assertEqual(
            [(e.name, e.model) for e in endpoints],
            [
                ("cerebras", "cerebras-test"),
                ("groq", "groq-test"),
                ("huggingface", "hf-test"),
                ("vercel", "vercel-test"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
