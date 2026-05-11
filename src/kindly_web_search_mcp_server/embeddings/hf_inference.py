"""Hugging Face Inference Provider embeddings."""

from __future__ import annotations

import logging
import os
from typing import Any

from huggingface_hub import AsyncInferenceClient, InferenceTimeoutError

from ..settings import settings

EMBEDDING_DIM = 1024
LOGGER = logging.getLogger(__name__)


class EmbeddingDimensionError(ValueError):
    """Raised when the embedding provider returns an unexpected vector size."""


class EmbeddingTimeoutError(RuntimeError):
    """Raised when embedding request exceeds timeout."""


class EmbeddingAPIError(RuntimeError):
    """Raised when embedding API request fails."""


def _as_list(value: Any) -> Any:
    return value.tolist() if hasattr(value, "tolist") else value


def _coerce_vectors(raw: Any, expected_count: int) -> list[list[float]]:
    data = _as_list(raw)
    if expected_count == 1 and data and all(isinstance(v, int | float) for v in data):
        data = [data]
    if not isinstance(data, list) or len(data) != expected_count:
        count = len(data) if isinstance(data, list) else "non-list"
        raise ValueError(f"HF Inference returned {count} vectors for {expected_count} inputs")

    vectors: list[list[float]] = []
    for index, item in enumerate(data):
        item = _as_list(item)
        if not isinstance(item, list) or not all(isinstance(v, int | float) for v in item):
            raise ValueError(f"HF Inference embedding at index {index} is not numeric")
        vectors.append([float(v) for v in item])
    return vectors


def _validate_dimensions(vectors: list[list[float]], expected_dim: int) -> None:
    for index, vector in enumerate(vectors):
        if len(vector) != expected_dim:
            raise EmbeddingDimensionError(
                f"Expected embedding dimension {expected_dim}, got {len(vector)} at index {index}"
            )


async def embed_texts(
    texts: list[str],
    *,
    model: str | None = None,
    provider: str | None = None,
    api_key: str | None = None,
    expected_dim: int | None = None,
    timeout: float | None = None,
    http_client: object | None = None,
) -> list[list[float]]:
    """Embed texts through Hugging Face Inference Providers.

    Args:
        texts: List of texts to embed
        model: Model ID override
        provider: Provider override
        api_key: API key override
        expected_dim: Expected embedding dimension
        timeout: Timeout in seconds (default: 30)
        http_client: Ignored - HF client manages its own connections

    Raises:
        EmbeddingTimeoutError: If request exceeds timeout
        EmbeddingAPIError: If API request fails
        EmbeddingDimensionError: If returned dimensions don't match expected
    """
    if http_client is not None:
        LOGGER.debug("http_client parameter ignored - HF client manages connections")

    if not texts:
        return []
    if any(not text.strip() for text in texts):
        raise ValueError("Cannot embed empty text")

    resolved_model = model or settings.hf_embedding_model
    resolved_provider = provider or settings.hf_inference_provider
    resolved_key = api_key or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACEHUB_API_TOKEN")
    resolved_dim = expected_dim or settings.embedding_dim
    resolved_timeout = timeout if timeout is not None else 30.0

    client = AsyncInferenceClient(
        provider=resolved_provider,  # type: ignore[arg-type]
        api_key=resolved_key,
        timeout=resolved_timeout,
    )

    try:
        raw = await client.feature_extraction(texts, model=resolved_model, normalize=True)  # type: ignore[arg-type]
    except InferenceTimeoutError as e:
        LOGGER.error(f"Embedding request timed out after {resolved_timeout}s for {len(texts)} texts")
        raise EmbeddingTimeoutError(
            f"Embedding request timed out after {resolved_timeout}s"
        ) from e
    except Exception as e:
        LOGGER.error(f"Embedding API request failed: {type(e).__name__}: {e}")
        raise EmbeddingAPIError(f"Embedding API request failed: {type(e).__name__}: {e}") from e

    vectors = _coerce_vectors(raw, len(texts))
    _validate_dimensions(vectors, resolved_dim)
    return vectors


async def embed_query(
    query: str,
    *,
    model: str | None = None,
    provider: str | None = None,
    api_key: str | None = None,
    expected_dim: int | None = None,
    timeout: float | None = None,
    http_client: object | None = None,
) -> list[float]:
    """Embed a single query through Hugging Face Inference Providers."""
    vectors = await embed_texts(
        [query],
        model=model,
        provider=provider,
        api_key=api_key,
        expected_dim=expected_dim,
        timeout=timeout,
        http_client=http_client,
    )
    return vectors[0]
