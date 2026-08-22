"""Tests for Unified ML embedding client and dispatcher."""

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kindly_web_search_mcp_server.embeddings import (
    CircuitOpenError,
    EmbeddingAPIError,
    EmbeddingDimensionError,
    EmbeddingTimeoutError,
    embed_query,
    embed_texts,
    reset_embedding_clients,
)
from kindly_web_search_mcp_server.embeddings.unified_ml import (
    UNIFIEDML_CIRCUIT_BREAKER,
    _coerce_vectors,
    _format_query_with_prefix,
    _validate_dimensions,
)
from kindly_web_search_mcp_server.settings import settings


class TestUnifiedMLVectorCoercion(unittest.TestCase):
    def test_openai_format_coercion(self) -> None:
        raw = {
            "object": "list",
            "data": [
                {"object": "embedding", "index": 1, "embedding": [0.4, 0.5, 0.6]},
                {"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]},
            ],
            "model": "intfloat/multilingual-e5-small",
        }
        vectors = _coerce_vectors(raw, 2)
        self.assertEqual(len(vectors), 2)
        self.assertEqual(vectors[0], [0.1, 0.2, 0.3])
        self.assertEqual(vectors[1], [0.4, 0.5, 0.6])

    def test_fastembed_format_coercion(self) -> None:
        raw = {
            "embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
            "model": "intfloat/multilingual-e5-small",
            "dimensions": 3,
        }
        vectors = _coerce_vectors(raw, 2)
        self.assertEqual(len(vectors), 2)
        self.assertEqual(vectors[0], [0.1, 0.2, 0.3])

    def test_tei_format_coercion(self) -> None:
        raw = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        vectors = _coerce_vectors(raw, 2)
        self.assertEqual(len(vectors), 2)
        self.assertEqual(vectors[0], [0.1, 0.2, 0.3])

    def test_count_mismatch_raises(self) -> None:
        raw = {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}
        with self.assertRaises(ValueError):
            _coerce_vectors(raw, 2)

    def test_validate_dimensions(self) -> None:
        _validate_dimensions([[0.1, 0.2, 0.3]], 3)
        with self.assertRaises(EmbeddingDimensionError):
            _validate_dimensions([[0.1, 0.2]], 3)


class TestQueryPrefixFormatting(unittest.TestCase):
    def test_standard_e5_query_prefix(self) -> None:
        query = "best search engines"
        formatted = _format_query_with_prefix(query, "intfloat/multilingual-e5-small")
        self.assertEqual(formatted, "query: best search engines")

    def test_instruct_e5_query_prefix(self) -> None:
        query = "best search engines"
        formatted = _format_query_with_prefix(query, "intfloat/multilingual-e5-large-instruct")
        self.assertTrue(formatted.startswith("Instruct:"))
        self.assertTrue(formatted.endswith("Query: best search engines"))

    def test_non_e5_query_prefix(self) -> None:
        query = "best search engines"
        formatted = _format_query_with_prefix(query, "text-embedding-3-small")
        self.assertEqual(formatted, "best search engines")


class TestUnifiedMLEmbeddingsAsync(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        UNIFIEDML_CIRCUIT_BREAKER.reset()
        await reset_embedding_clients()

    async def asyncTearDown(self) -> None:
        UNIFIEDML_CIRCUIT_BREAKER.reset()
        await reset_embedding_clients()

    async def test_embed_texts_mocked_success(self) -> None:
        mock_response = httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                    {"index": 1, "embedding": [0.4, 0.5, 0.6]},
                ]
            },
            request=httpx.Request("POST", "http://127.0.0.1:8000/v1/embeddings"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        res = await embed_texts(
            ["hello", "world"],
            provider="unifiedml",
            expected_dim=3,
            http_client=mock_client,
        )
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0], [0.1, 0.2, 0.3])
        self.assertEqual(res[1], [0.4, 0.5, 0.6])

    async def test_embed_texts_zero_pads_shorter_vectors_to_requested_dimension(self) -> None:
        mock_response = httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]},
            request=httpx.Request("POST", "http://127.0.0.1:8000/v1/embeddings"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        vector = await embed_texts(
            ["hello"],
            provider="unifiedml",
            expected_dim=5,
            http_client=mock_client,
        )

        self.assertEqual(vector, [[0.1, 0.2, 0.3, 0.0, 0.0]])

    async def test_embed_query_mocked_success(self) -> None:
        mock_response = httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                ]
            },
            request=httpx.Request("POST", "http://127.0.0.1:8000/v1/embeddings"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        vec = await embed_query(
            "hello",
            provider="unifiedml",
            expected_dim=3,
            http_client=mock_client,
        )
        self.assertEqual(vec, [0.1, 0.2, 0.3])
        # Verify query prefix was applied in the request
        call_args = mock_client.post.call_args
        payload = call_args[1].get("json", {})
        self.assertEqual(payload["input"], ["query: hello"])

    async def test_empty_text_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            await embed_texts(["   "], provider="unifiedml")

    async def test_oversized_vector_raises_dimension_error(self) -> None:
        mock_response = httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3, 0.4]}]},
            request=httpx.Request("POST", "http://127.0.0.1:8000/v1/embeddings"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        with self.assertRaises(EmbeddingDimensionError):
            await embed_texts(
                ["hello"],
                provider="unifiedml",
                expected_dim=3,
                http_client=mock_client,
            )

    async def test_timeout_raises_embedding_timeout_error(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
        mock_client.is_closed = False

        with patch.object(settings, "embedding_max_retries", 0):
            with self.assertRaises(EmbeddingTimeoutError):
                await embed_texts(
                    ["hello"],
                    provider="unifiedml_only",
                    expected_dim=384,
                    http_client=mock_client,
                )

    async def test_circuit_breaker_trips_after_failures(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=httpx.HTTPError("Server down"))
        mock_client.is_closed = False

        with patch.object(settings, "embedding_max_retries", 0):
            for _ in range(3):
                try:
                    await embed_texts(
                        ["hello"],
                        provider="unifiedml_only",
                        expected_dim=384,
                        http_client=mock_client,
                    )
                except (EmbeddingAPIError, CircuitOpenError):
                    pass

            self.assertTrue(UNIFIEDML_CIRCUIT_BREAKER.is_open())
            with self.assertRaises(CircuitOpenError):
                await embed_texts(
                    ["hello"],
                    provider="unifiedml_only",
                    expected_dim=786,
                    http_client=mock_client,
                )


class TestLiveUnifiedMLEmbeddings(unittest.IsolatedAsyncioTestCase):
    """Integration test verifying against the actual live Unified ML service on port 8000."""

    async def test_live_unifiedml_embeddings(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                health = await client.get(f"{settings.embedding_endpoint_url}/health")
                if health.status_code != 200:
                    self.skipTest("Unified ML service not available on port 8000")
        except Exception:
            self.skipTest("Unified ML service not reachable on port 8000")

        # Test live embed_query
        q_vec = await embed_query("how to deploy docker containers")
        self.assertEqual(len(q_vec), 786)
        self.assertTrue(all(isinstance(x, float) for x in q_vec))

        # Test live embed_texts
        t_vecs = await embed_texts(["docker in production", "fastembed onnx inference"])
        self.assertEqual(len(t_vecs), 2)
        self.assertEqual(len(t_vecs[0]), 786)
        self.assertEqual(len(t_vecs[1]), 786)


if __name__ == "__main__":
    unittest.main()
