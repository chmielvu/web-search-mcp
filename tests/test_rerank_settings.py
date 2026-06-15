from __future__ import annotations

import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestRerankSettings(unittest.TestCase):
    def test_rerank_stack_mode_defaults_to_full_stack(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            import kindly_web_search_mcp_server.settings as settings_module

            reloaded = importlib.reload(settings_module)

        self.assertEqual(reloaded.settings.rerank_stack_mode, "bi_cross_llm")
        self.assertEqual(reloaded.settings.rerank_llm_candidate_limit, 20)
        self.assertEqual(reloaded.settings.rerank_llm_timeout_seconds, 20.0)

    def test_rerank_stack_mode_reads_env(self) -> None:
        with patch.dict(os.environ, {"RERANK_STACK_MODE": "bi_cross_llm"}, clear=False):
            import kindly_web_search_mcp_server.settings as settings_module

            reloaded = importlib.reload(settings_module)

        self.assertEqual(reloaded.settings.rerank_stack_mode, "bi_cross_llm")

    def test_invalid_rerank_stack_mode_fails_fast(self) -> None:
        import kindly_web_search_mcp_server.settings as settings_module

        with patch.dict(os.environ, {"RERANK_STACK_MODE": "invalid-mode"}, clear=False):
            with self.assertRaises(ValueError):
                importlib.reload(settings_module)
