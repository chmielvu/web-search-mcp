"""Unified ML embedding client for local/VPS ONNX inference service."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
import httpx

from ..settings import settings
from .hf_inference import (
    CircuitOpenError,
    EmbeddingAPIError,
    EmbeddingDimensionError,
    EmbeddingTimeoutError,
    HFCircuitBreaker,
)

EMBEDDING_DIM = 786
LOGGER = logging.getLogger(__name__)

# E5-instruct requires task instruction; standard E5 uses "query: " prefix.
_E5_INSTRUCT_TASK = "Given a web search query, retrieve relevant passages that answer the query"


def _format_query_with_prefix(query: str, model: str) -> str:
    """Format a query with appropriate prefix based on model family."""
    if "instruct" in model.lower():
        return f"Instruct: {_E5_INSTRUCT_TASK}\nQuery: {query}"
    if "e5" in model.lower():
        return f"query: {query}"
    return query


class UnifiedMLCircuitBreaker(HFCircuitBreaker):
    """Circuit breaker for Unified ML service."""


# Global circuit breaker instance for Unified ML
UNIFIEDML_CIRCUIT_BREAKER = UnifiedMLCircuitBreaker()

# Singleton AsyncClient for connection reuse
_UNIFIEDML_CLIENT: httpx.AsyncClient | None = None
_UNIFIEDML_CLIENT_LOCK = asyncio.Lock()


async def _get_unifiedml_client() -> httpx.AsyncClient:
    """Return a singleton httpx.AsyncClient, creating it if needed."""
    global _UNIFIEDML_CLIENT
    if _UNIFIEDML_CLIENT is not None and not _UNIFIEDML_CLIENT.is_closed:
        return _UNIFIEDML_CLIENT

    async with _UNIFIEDML_CLIENT_LOCK:
        if _UNIFIEDML_CLIENT is not None and not _UNIFIEDML_CLIENT.is_closed:
            return _UNIFIEDML_CLIENT

        timeout = httpx.Timeout(
            timeout=settings.embedding_timeout_seconds,
            connect=10.0,
            read=settings.embedding_timeout_seconds,
            write=10.0,
        )
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)
        _UNIFIEDML_CLIENT = httpx.AsyncClient(timeout=timeout, limits=limits)
        return _UNIFIEDML_CLIENT


async def reset_unifiedml_client() -> None:
    """Close and clear the shared Unified ML client after cancellation or connection issues."""
    global _UNIFIEDML_CLIENT
    async with _UNIFIEDML_CLIENT_LOCK:
        if _UNIFIEDML_CLIENT is not None:
            client = _UNIFIEDML_CLIENT
            _UNIFIEDML_CLIENT = None
            if not client.is_closed:
                await client.aclose()


def _coerce_vectors(data: Any, expected_count: int) -> list[list[float]]:
    """Coerce raw response to list of float vectors."""
    vectors: list[list[float]] = []
    if isinstance(data, dict):
        if "data" in data and isinstance(data["data"], list):
            # OpenAI format: {"data": [{"embedding": [...]}]}
            items = sorted(data["data"], key=lambda x: x.get("index", 0))
            for item in items:
                vectors.append([float(x) for x in item["embedding"]])
        elif "embeddings" in data and isinstance(data["embeddings"], list):
            # FastEmbed format: {"embeddings": [[...]]}
            for emb in data["embeddings"]:
                vectors.append([float(x) for x in emb])
    elif isinstance(data, list):
        # TEI format: [[...], [...]] or [{"embedding": [...]}]
        for item in data:
            if isinstance(item, dict) and "embedding" in item:
                vectors.append([float(x) for x in item["embedding"]])
            elif isinstance(item, list):
                vectors.append([float(x) for x in item])

    if len(vectors) != expected_count:
        raise ValueError(
            f"Expected {expected_count} embedding vectors from Unified ML, got {len(vectors)}"
        )
    return vectors


def _validate_dimensions(vectors: list[list[float]], expected_dim: int) -> None:
    """Validate that all vectors match expected dimension."""
    for index, vector in enumerate(vectors):
        if len(vector) != expected_dim:
            raise EmbeddingDimensionError(
                f"Embedding vector at index {index} has dimension {len(vector)}, "
                f"expected {expected_dim}"
            )


def _pad_shorter_vectors(
    vectors: list[list[float]], expected_dim: int
) -> list[list[float]]:
    """Pad Unified ML's short vectors without discarding any dimensions."""
    return [
        vector + [0.0] * (expected_dim - len(vector))
        if 0 < len(vector) < expected_dim
        else vector
        for vector in vectors
    ]


async def embed_texts(
    texts: list[str],
    *,
    model: str | None = None,
    base_url: str | None = None,
    expected_dim: int | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
    http_client: httpx.AsyncClient | None = None,
    skip_circuit_check: bool = False,
) -> list[list[float]]:
    """Embed texts through Unified ML inference service.

    Args:
        texts: List of texts to embed
        model: Model ID override
        base_url: Base URL override (default: settings.embedding_endpoint_url)
        expected_dim: Expected embedding dimension
        timeout: Timeout in seconds
        max_retries: Retry count
        http_client: Optional httpx.AsyncClient override
        skip_circuit_check: Skip circuit breaker check

    Raises:
        EmbeddingTimeoutError: If request exceeds timeout
        EmbeddingAPIError: If API request fails
        EmbeddingDimensionError: If returned dimensions don't match expected
        CircuitOpenError: If circuit breaker is open and blocking calls
    """
    if not texts:
        return []
    if any(not text.strip() for text in texts):
        raise ValueError("Cannot embed empty text")

    # Circuit breaker check
    if not skip_circuit_check and UNIFIEDML_CIRCUIT_BREAKER.is_open():
        LOGGER.warning(
            f"Circuit breaker OPEN - skipping Unified ML embedding for {len(texts)} texts. "
            f"State: {UNIFIEDML_CIRCUIT_BREAKER.get_state()}, failures: {UNIFIEDML_CIRCUIT_BREAKER.get_failure_count()}"
        )
        raise CircuitOpenError(
            f"Unified ML embedding circuit breaker is open. State: {UNIFIEDML_CIRCUIT_BREAKER.get_state()}, "
            f"failures: {UNIFIEDML_CIRCUIT_BREAKER.get_failure_count()}"
        )

    resolved_base_url = (base_url or settings.embedding_endpoint_url).rstrip("/")
    resolved_model = model or settings.embedding_model
    resolved_dim = expected_dim or settings.embedding_dim
    resolved_timeout = timeout if timeout is not None else settings.embedding_timeout_seconds
    resolved_retries = max_retries if max_retries is not None else settings.embedding_max_retries
    retry_delay = settings.embedding_retry_delay_seconds

    endpoint = f"{resolved_base_url}/v1/embeddings"
    payload = {
        "input": texts,
        "model": resolved_model,
    }

    client = http_client or await _get_unifiedml_client()
    raw_data: Any = None

    for attempt in range(resolved_retries + 1):
        try:
            resp = await asyncio.wait_for(
                client.post(endpoint, json=payload),
                timeout=resolved_timeout,
            )
            resp.raise_for_status()
            raw_data = resp.json()
            UNIFIEDML_CIRCUIT_BREAKER.record_success()
            break
        except (asyncio.TimeoutError, httpx.TimeoutException) as e:
            UNIFIEDML_CIRCUIT_BREAKER.record_failure()
            if attempt < resolved_retries:
                LOGGER.warning(
                    "Unified ML embedding timeout attempt %d/%d, retrying in %.1fs",
                    attempt + 1,
                    resolved_retries + 1,
                    retry_delay,
                )
                await asyncio.sleep(retry_delay)
            else:
                LOGGER.error(
                    "Unified ML embedding timed out after %d attempts",
                    resolved_retries + 1,
                )
                raise EmbeddingTimeoutError(
                    f"Unified ML embedding request timed out ({resolved_retries + 1} attempts)"
                ) from e
        except Exception as e:
            LOGGER.error(f"Unified ML embedding API request failed: {type(e).__name__}: {e}")
            UNIFIEDML_CIRCUIT_BREAKER.record_failure()
            raise EmbeddingAPIError(
                f"Unified ML embedding API request failed: {type(e).__name__}: {e}"
            ) from e

    assert raw_data is not None
    vectors = _pad_shorter_vectors(_coerce_vectors(raw_data, len(texts)), resolved_dim)
    _validate_dimensions(vectors, resolved_dim)
    return vectors


async def embed_query(
    query: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    expected_dim: int | None = None,
    timeout: float | None = None,
    http_client: httpx.AsyncClient | None = None,
    skip_circuit_check: bool = False,
) -> list[float]:
    """Embed a single query through Unified ML inference service.

    For E5 models, the query is automatically formatted with the appropriate prefix.
    """
    resolved_model = model or settings.embedding_model
    formatted_query = _format_query_with_prefix(query, resolved_model)
    vectors = await embed_texts(
        [formatted_query],
        model=resolved_model,
        base_url=base_url,
        expected_dim=expected_dim,
        timeout=timeout,
        http_client=http_client,
        skip_circuit_check=skip_circuit_check,
    )
    return vectors[0]
