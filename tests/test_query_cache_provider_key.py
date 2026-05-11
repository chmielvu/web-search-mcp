from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestQueryCacheProviderKey(unittest.TestCase):
    def test_provider_cache_key_is_order_insensitive(self) -> None:
        from kindly_web_search_mcp_server.cache.query_cache import provider_cache_key

        self.assertEqual(
            provider_cache_key(["jina", "ddg"]),
            provider_cache_key(["ddg", "jina"]),
        )

    def test_exact_cache_key_differs_by_provider_set(self) -> None:
        from kindly_web_search_mcp_server.cache.query_cache import _compute_cache_key

        default_key = _compute_cache_key(
            "python docs",
            5,
            True,
            "balanced",
            providers_key="default",
        )
        jina_key = _compute_cache_key(
            "python docs",
            5,
            True,
            "balanced",
            providers_key="jina",
        )

        self.assertNotEqual(default_key, jina_key)


if __name__ == "__main__":
    unittest.main()
