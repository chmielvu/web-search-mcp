"""Tests for the four generic extraction stage functions in stages.py."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from kindly_web_search_mcp_server.content.artifact import ContentArtifact
from kindly_web_search_mcp_server.content.options import FetchOptions
from kindly_web_search_mcp_server.content.status_classifier import ClassificationResult
from kindly_web_search_mcp_server.content.jina_reader import JinaReaderError
from kindly_web_search_mcp_server.content import stages


class TestJinaStage(unittest.IsolatedAsyncioTestCase):
    async def test_returns_none_on_transport_failure(self) -> None:
        with patch.object(stages, "fetch_with_jina_reader", side_effect=JinaReaderError("down")):
            result = await stages._fetch_via_jina("http://example.com", options=FetchOptions())
            self.assertIsNone(result)

    async def test_returns_artifact_on_success(self) -> None:
        cr = ClassificationResult(status="success", reason=None, cacheable=True)
        with (
            patch.object(stages, "fetch_with_jina_reader", return_value="# OK"),
            patch.object(stages, "classify_markdown", return_value=cr),
            patch.object(stages, "record_content_resolution"),
        ):
            result = await stages._fetch_via_jina("http://example.com", options=FetchOptions())
            self.assertIsInstance(result, ContentArtifact)
            self.assertEqual(result.fetch_backend, "jina_reader")


class TestLocalStage(unittest.IsolatedAsyncioTestCase):
    async def test_always_returns_artifact(self) -> None:
        with patch.object(stages, "safe_fetch_url", side_effect=Exception("boom")):
            result = await stages._fetch_via_local("http://example.com", options=FetchOptions())
            self.assertIsInstance(result, ContentArtifact)
            self.assertEqual(result.status, "error")


class TestCrawl4AIStage(unittest.IsolatedAsyncioTestCase):
    async def test_raises_when_client_unconfigured(self) -> None:
        from kindly_web_search_mcp_server.content.remote_clients import Crawl4AIClientError

        with patch.object(stages, "get_crawl4ai_client", return_value=None):
            with self.assertRaises(Crawl4AIClientError):
                await stages._fetch_via_crawl4ai("http://example.com", FetchOptions())


class TestCamoufoxStage(unittest.IsolatedAsyncioTestCase):
    async def test_raises_when_client_unconfigured(self) -> None:
        from kindly_web_search_mcp_server.content.remote_clients import CamoufoxClientError

        with patch.object(stages, "get_camoufox_client", return_value=None):
            with self.assertRaises(CamoufoxClientError):
                await stages._fetch_via_camoufox("http://example.com", FetchOptions())
