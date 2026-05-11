from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class _FakeAsyncInferenceClient:
    def __init__(self, *, provider: str, token: str | None = None, api_key: str | None = None, timeout: float | None = None) -> None:
        self.provider = provider
        self.token = token
        self.api_key = api_key
        self.timeout = timeout

    async def feature_extraction(self, text, *, model: str, normalize: bool = True):
        return [[0.1, 0.2, 0.3] for _ in text]


class _BadDimensionClient(_FakeAsyncInferenceClient):
    async def feature_extraction(self, text, *, model: str, normalize: bool = True):
        return [[0.1, 0.2]]


class TestHfInferenceEmbeddings(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
