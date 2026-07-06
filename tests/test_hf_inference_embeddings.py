from __future__ import annotations

import sys
import unittest
import asyncio
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class _FakeAsyncInferenceClient:
    def __init__(
        self,
        *,
        provider: str,
        token: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.provider = provider
        self.token = token
        self.api_key = api_key
        self.timeout = timeout

    async def feature_extraction(self, text, *, model: str, normalize: bool = True):
        return [[0.1, 0.2, 0.3] for _ in text]


class _BadDimensionClient(_FakeAsyncInferenceClient):
    async def feature_extraction(self, text, *, model: str, normalize: bool = True):
        return [[0.1, 0.2]]


class _SlowAsyncInferenceClient(_FakeAsyncInferenceClient):
    calls = 0

    async def feature_extraction(self, text, *, model: str, normalize: bool = True):
        type(self).calls += 1
        await asyncio.sleep(1)
        return [[0.1, 0.2, 0.3] for _ in text]


class _ClosableAsyncInferenceClient(_FakeAsyncInferenceClient):
    closed_count = 0

    def close(self) -> None:
        type(self).closed_count += 1


class _ConcurrencyTrackingClient(_FakeAsyncInferenceClient):
    active = 0
    peak = 0

    async def feature_extraction(self, text, *, model: str, normalize: bool = True):
        type(self).active += 1
        type(self).peak = max(type(self).peak, type(self).active)
        await asyncio.sleep(0.05)
        type(self).active -= 1
        return [[0.1, 0.2, 0.3] for _ in text]


class TestHfInferenceEmbeddings(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        from kindly_web_search_mcp_server.embeddings import hf_inference

        await hf_inference.reset_hf_client()

    async def test_embed_texts_uses_hf_inference_provider_and_validates_dimension(self) -> None:
        from kindly_web_search_mcp_server.embeddings.hf_inference import embed_texts

        with patch(
            "kindly_web_search_mcp_server.embeddings.hf_inference.AsyncInferenceClient",
            _FakeAsyncInferenceClient,
        ):
            vectors = await embed_texts(
                ["alpha", "beta"],
                model="BAAI/bge-m3",
                provider="hf-inference",
                api_key="hf-test-key",
                expected_dim=3,
            )

        self.assertEqual(vectors, [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]])

    async def test_embed_texts_rejects_wrong_dimension_instead_of_zero_filling(self) -> None:
        from kindly_web_search_mcp_server.embeddings.hf_inference import (
            EmbeddingDimensionError,
            embed_texts,
        )

        with patch(
            "kindly_web_search_mcp_server.embeddings.hf_inference.AsyncInferenceClient",
            _BadDimensionClient,
        ):
            with self.assertRaises(EmbeddingDimensionError):
                await embed_texts(
                    ["alpha"],
                    model="BAAI/bge-m3",
                    provider="hf-inference",
                    api_key="hf-test-key",
                    expected_dim=3,
                )

    async def test_embed_texts_enforces_per_call_timeout(self) -> None:
        from kindly_web_search_mcp_server.embeddings.hf_inference import (
            EmbeddingTimeoutError,
            embed_texts,
        )

        with (
            patch(
                "kindly_web_search_mcp_server.embeddings.hf_inference.AsyncInferenceClient",
                _SlowAsyncInferenceClient,
            ),
            patch("kindly_web_search_mcp_server.embeddings.hf_inference.settings") as mock_settings,
        ):
            mock_settings.embedding_max_retries = 0
            mock_settings.embedding_retry_delay_seconds = 0.0
            mock_settings.hf_embedding_model = "BAAI/bge-m3"
            mock_settings.embedding_dim = 3
            mock_settings.hf_inference_provider = "hf-inference"
            mock_settings.embedding_timeout_seconds = 30.0
            with self.assertRaises(EmbeddingTimeoutError):
                await embed_texts(
                    ["alpha"],
                    model="BAAI/bge-m3",
                    provider="hf-inference",
                    api_key="hf-test-key",
                    expected_dim=3,
                    timeout=0.01,
                    skip_circuit_check=True,
                )

    async def test_embed_texts_can_disable_retries_for_latency_sensitive_calls(self) -> None:
        from kindly_web_search_mcp_server.embeddings.hf_inference import (
            EmbeddingTimeoutError,
            embed_texts,
        )

        _SlowAsyncInferenceClient.calls = 0
        with patch(
            "kindly_web_search_mcp_server.embeddings.hf_inference.AsyncInferenceClient",
            _SlowAsyncInferenceClient,
        ):
            with self.assertRaises(EmbeddingTimeoutError):
                await embed_texts(
                    ["alpha"],
                    model="BAAI/bge-m3",
                    provider="hf-inference",
                    api_key="hf-test-key",
                    expected_dim=3,
                    timeout=0.01,
                    max_retries=0,
                    skip_circuit_check=True,
                )

        self.assertEqual(_SlowAsyncInferenceClient.calls, 1)

    async def test_reset_hf_client_closes_singleton(self) -> None:
        from kindly_web_search_mcp_server.embeddings import hf_inference
        from kindly_web_search_mcp_server.embeddings.hf_inference import embed_texts

        _ClosableAsyncInferenceClient.closed_count = 0
        with patch(
            "kindly_web_search_mcp_server.embeddings.hf_inference.AsyncInferenceClient",
            _ClosableAsyncInferenceClient,
        ):
            await embed_texts(
                ["alpha"],
                model="BAAI/bge-m3",
                provider="hf-inference",
                api_key="hf-test-key",
                expected_dim=3,
            )
            await hf_inference.reset_hf_client()

        self.assertEqual(_ClosableAsyncInferenceClient.closed_count, 1)

    async def test_embed_texts_allows_concurrent_hf_provider_requests(self) -> None:
        from kindly_web_search_mcp_server.embeddings.hf_inference import embed_texts

        _ConcurrencyTrackingClient.active = 0
        _ConcurrencyTrackingClient.peak = 0
        with patch(
            "kindly_web_search_mcp_server.embeddings.hf_inference.AsyncInferenceClient",
            _ConcurrencyTrackingClient,
        ):
            await asyncio.gather(
                embed_texts(
                    ["alpha"],
                    model="BAAI/bge-m3",
                    provider="hf-inference",
                    api_key="hf-test-key",
                    expected_dim=3,
                ),
                embed_texts(
                    ["beta"],
                    model="BAAI/bge-m3",
                    provider="hf-inference",
                    api_key="hf-test-key",
                    expected_dim=3,
                ),
            )

        self.assertGreaterEqual(_ConcurrencyTrackingClient.peak, 2)


if __name__ == "__main__":
    unittest.main()
