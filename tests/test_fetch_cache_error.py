from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestFetchCacheError(unittest.TestCase):
    def test_artifact_from_cache_restores_error(self) -> None:
        from kindly_web_search_mcp_server.tools.content import _artifact_from_cache

        error = {
            "code": "fallback_fetch_failed",
            "message": "upstream failed",
            "retryable": True,
        }
        cached = {
            "page_content": "x",
            "extraction_method": "jina_reader",
            "metadata": {
                "__web_fetch__": {
                    "schema_version": 3,
                    "route_version": 2,
                    "normalized_url": "https://example.com/failed",
                    "fetched_url": "https://example.com/failed",
                    "status": "error",
                    "source_type": "html",
                    "content_type": "text/markdown",
                    "origin_backend": "jina_reader",
                    "error": error,
                }
            },
        }
        artifact = _artifact_from_cache(
            "https://example.com/failed", "https://example.com/failed", cached
        )
        self.assertEqual(artifact["status"], "error")
        self.assertEqual(artifact["error"], error)
        self.assertEqual(artifact["markdown"], "x")

    def test_success_envelope_clears_leftover_error(self) -> None:
        from kindly_web_search_mcp_server.tools.content import (
            _artifact_from_cache,
            _result_from_artifact,
        )

        cached = {
            "page_content": "long enough article body " * 20,
            "extraction_method": "jina_reader",
            "metadata": {
                "__web_fetch__": {
                    "schema_version": 3,
                    "route_version": 2,
                    "normalized_url": "https://example.com/ok",
                    "fetched_url": "https://example.com/ok",
                    "status": "success",
                    "source_type": "html",
                    "content_type": "text/markdown",
                    "origin_backend": "jina_reader",
                    "error": {"code": "error_page:404 not found", "message": "stale", "retryable": False},
                }
            },
        }
        artifact = _artifact_from_cache(
            "https://example.com/ok", "https://example.com/ok", cached
        )
        self.assertEqual(artifact["status"], "success")
        self.assertIsNone(artifact["error"])
        result = _result_from_artifact(
            artifact, offset=0, max_chars=0, include_metadata=True, include_links=False
        )
        self.assertIsNone(result["error"])


if __name__ == "__main__":
    unittest.main()
