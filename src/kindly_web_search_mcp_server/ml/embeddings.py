"""Embedding client for the fastembed-snowflake service (VPS, port 8001).

Service: FastAPI + FastEmbed ONNX running ``snowflake/snowflake-arctic-embed-s``
(384-d, cosine-normalized vectors). Contract (verified live 2026-09-07):

- ``POST /embed``  ``{"texts": [str]}`` -> ``{"embeddings": [[f]], "model": str, "dimension": int}``
- batch cap 256 texts, 8192 chars/text, 2 MiB body
- binds ``127.0.0.1`` on the VPS only — reach it through an SSH tunnel
  (``ssh -L 8001:127.0.0.1:8001``); default endpoint is ``http://127.0.0.1:8001``.

The service always normalizes (``normalize=false`` is rejected server-side),
so no query/passage prefixing is applied.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ..settings import settings

EMBEDDING_DIM = 384
LOGGER = logging.getLogger(__name__)


class EmbeddingDimensionError(ValueError):
    """Raised when the embedding provider returns an unexpected vector size."""


class EmbeddingTimeoutError(RuntimeError):
    """Raised when embedding request exceeds timeout."""


class EmbeddingAPIError(RuntimeError):
    """Raised when embedding API request fails."""


# Singleton AsyncClient for connection reuse
_CLIENT: httpx.AsyncClient | None = None
_CLIENT_LOCK = asyncio.Lock()


async def _get_client() -> httpx.AsyncClient:
    """Return a singleton httpx.AsyncClient, creating it if needed."""
    global _CLIENT
    async with _CLIENT_LOCK:
        if _CLIENT is None or _CLIENT.is_closed:
            _CLIENT = httpx.AsyncClient(
                timeout=httpx.Timeout(settings.embedding_timeout_seconds),
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            )
        return _CLIENT


async def reset_client() -> None:
    """Close and clear the shared fastembed client."""
    global _CLIENT
    async with _CLIENT_LOCK:
        if _CLIENT is not None and not _CLIENT.is_closed:
            await _CLIENT.aclose()
        _CLIENT = None


def _coerce_vectors(data: Any, expected_count: int) -> list[list[float]]:
    """Coerce raw response to list of float vectors."""
    if not isinstance(data, dict):
        raise EmbeddingAPIError("embedding response must be a JSON object")
    vectors = data.get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != expected_count:
        raise EmbeddingAPIError(
            f"embedding response must contain {expected_count} vectors, got "
            f"{len(vectors) if isinstance(vectors, list) else type(vectors).__name__}"
        )
    out: list[list[float]] = []
    for vector in vectors:
        if not isinstance(vector, list):
            raise EmbeddingAPIError("embedding vector must be a list")
        out.append([float(x) for x in vector])
    return out


def _validate_dimensions(vectors: list[list[float]], expected_dim: int) -> None:
    """Validate that all vectors match expected dimension."""
    for vector in vectors:
        if len(vector) != expected_dim:
            raise EmbeddingDimensionError(
                f"Embedding dimension mismatch: expected {expected_dim}, got {len(vector)}"
            )


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
    """Embed texts through the fastembed-snowflake service.

    Args:
        texts: List of texts to embed (batch cap enforced server-side at 256)
        model: Ignored (service pins ``snowflake/snowflake-arctic-embed-s``); accepted for contract parity
        base_url: Base URL override (default: settings.embedding_endpoint_url)
        expected_dim: Expected embedding dimension (default 384)
        timeout: Timeout in seconds
        max_retries: Retry count
        http_client: Optional httpx.AsyncClient override
        skip_circuit_check: Ignored (no circuit breaker); accepted for contract parity

    Raises:
        EmbeddingTimeoutError: If request exceeds timeout
        EmbeddingAPIError: If API request fails
        EmbeddingDimensionError: If returned dimensions don't match expected
    """
    del model, skip_circuit_check  # contract parity; service pins the model
    if not texts:
        return []
    if any(not text.strip() for text in texts):
        raise ValueError("Cannot embed empty text")

    resolved_base_url = (base_url or settings.embedding_endpoint_url).rstrip("/")
    resolved_dim = expected_dim or settings.embedding_dim or EMBEDDING_DIM
    resolved_timeout = timeout if timeout is not None else settings.embedding_timeout_seconds
    resolved_retries = max_retries if max_retries is not None else settings.embedding_max_retries
    retry_delay = settings.embedding_retry_delay_seconds

    endpoint = f"{resolved_base_url}/embed"
    payload = {"texts": texts}

    client = http_client or await _get_client()
    raw_data: Any = None

    for attempt in range(resolved_retries + 1):
        try:
            resp = await asyncio.wait_for(
                client.post(endpoint, json=payload),
                timeout=resolved_timeout,
            )
            resp.raise_for_status()
            raw_data = resp.json()
            break
        except (TimeoutError, httpx.TimeoutException) as e:
            if attempt < resolved_retries:
                LOGGER.warning(
                    "fastembed embedding timeout attempt %d/%d, retrying in %.1fs",
                    attempt + 1,
                    resolved_retries + 1,
                    retry_delay,
                )
                await asyncio.sleep(retry_delay)
            else:
                LOGGER.error(
                    "fastembed embedding timed out after %d attempts",
                    resolved_retries + 1,
                )
                raise EmbeddingTimeoutError(
                    f"fastembed embedding request timed out ({resolved_retries + 1} attempts)"
                ) from e
        except Exception as e:
            LOGGER.error(f"fastembed embedding API request failed: {type(e).__name__}: {e}")
            raise EmbeddingAPIError(
                f"fastembed embedding API request failed: {type(e).__name__}: {e}"
            ) from e

    if raw_data is None:
        raise EmbeddingAPIError("embedding request produced no response")
    vectors = _coerce_vectors(raw_data, len(texts))
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
    """Embed a single query through the fastembed-snowflake service."""
    vectors = await embed_texts(
        [query],
        model=model,
        base_url=base_url,
        expected_dim=expected_dim,
        timeout=timeout,
        http_client=http_client,
        skip_circuit_check=skip_circuit_check,
    )
    return vectors[0]
