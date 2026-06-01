"""Hugging Face Inference Provider embeddings."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from huggingface_hub import AsyncInferenceClient, InferenceTimeoutError

from ..settings import settings

EMBEDDING_DIM = 384  # ibm-granite/granite-embedding-97m-multilingual-r2 dimension
LOGGER = logging.getLogger(__name__)


class EmbeddingDimensionError(ValueError):
    """Raised when the embedding provider returns an unexpected vector size."""


class EmbeddingTimeoutError(RuntimeError):
    """Raised when embedding request exceeds timeout."""


class EmbeddingAPIError(RuntimeError):
    """Raised when embedding API request fails."""


class CircuitOpenError(RuntimeError):
    """Raised when circuit breaker is open and calls are blocked."""


class HFCircuitBreaker:
    """
    Circuit breaker for HF embedding calls.

    Opens after 3 consecutive failures, auto-recovers after 60 seconds.
    Prevents cascading timeouts during HF inference instability.

    States:
    - CLOSED: Normal operation, calls pass through
    - OPEN: Failures exceeded threshold, calls blocked
    - HALF_OPEN: Recovery timeout elapsed, single test call allowed
    """

    FAILURE_THRESHOLD = 3
    RECOVERY_TIMEOUT_SECONDS = 60.0

    def __init__(self) -> None:
        self._failure_count: int = 0
        self._last_failure_time: float = 0.0
        self._state: str = "closed"  # closed, open, half_open
        self._half_open_success: bool = False

    def is_open(self) -> bool:
        """Check if circuit is open (calls should be blocked)."""
        if self._state == "closed":
            return False

        if self._state == "open":
            # Check if recovery timeout elapsed
            elapsed = time.time() - self._last_failure_time
            if elapsed >= self.RECOVERY_TIMEOUT_SECONDS:
                LOGGER.info(
                    "Circuit breaker entering HALF_OPEN state after recovery timeout"
                )
                self._state = "half_open"
                self._half_open_success = False
                return False  # Allow one test call
            return True

        # half_open: allow one test call
        return self._half_open_success

    def record_success(self) -> None:
        """Record successful call, reset circuit."""
        if self._state == "half_open":
            LOGGER.info("Circuit breaker test call succeeded, returning to CLOSED")
            self._half_open_success = True

        self._failure_count = 0
        self._state = "closed"

    def record_failure(self) -> None:
        """Record failed call, potentially open circuit."""
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._state == "half_open":
            LOGGER.warning("Circuit breaker test call failed, returning to OPEN")
            self._state = "open"
            return

        if self._failure_count >= self.FAILURE_THRESHOLD:
            LOGGER.warning(
                f"Circuit breaker OPENED after {self._failure_count} consecutive failures. "
                f"Will auto-recover in {self.RECOVERY_TIMEOUT_SECONDS}s"
            )
            self._state = "open"

    def get_state(self) -> str:
        """Get current circuit state for telemetry."""
        return self._state

    def get_failure_count(self) -> int:
        """Get current failure count for telemetry."""
        return self._failure_count


# Global circuit breaker instance
HF_CIRCUIT_BREAKER = HFCircuitBreaker()


def _as_list(value: Any) -> Any:
    return value.tolist() if hasattr(value, "tolist") else value


def _coerce_vectors(raw: Any, expected_count: int) -> list[list[float]]:
    data = _as_list(raw)
    if expected_count == 1 and data and all(isinstance(v, int | float) for v in data):
        data = [data]
    if not isinstance(data, list) or len(data) != expected_count:
        count = len(data) if isinstance(data, list) else "non-list"
        raise ValueError(
            f"HF Inference returned {count} vectors for {expected_count} inputs"
        )

    vectors: list[list[float]] = []
    for index, item in enumerate(data):
        item = _as_list(item)
        if not isinstance(item, list) or not all(
            isinstance(v, int | float) for v in item
        ):
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
    skip_circuit_check: bool = False,
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
        skip_circuit_check: Skip circuit breaker check (for critical path embeddings)

    Raises:
        EmbeddingTimeoutError: If request exceeds timeout
        EmbeddingAPIError: If API request fails
        EmbeddingDimensionError: If returned dimensions don't match expected
        CircuitOpenError: If circuit breaker is open and blocking calls
    """
    if http_client is not None:
        LOGGER.debug("http_client parameter ignored - HF client manages connections")

    if not texts:
        return []
    if any(not text.strip() for text in texts):
        raise ValueError("Cannot embed empty text")

    # Circuit breaker check (unless skipped for critical path)
    if not skip_circuit_check and HF_CIRCUIT_BREAKER.is_open():
        LOGGER.warning(
            f"Circuit breaker OPEN - skipping embedding for {len(texts)} texts. "
            f"State: {HF_CIRCUIT_BREAKER.get_state()}, failures: {HF_CIRCUIT_BREAKER.get_failure_count()}"
        )
        raise CircuitOpenError(
            f"HF embedding circuit breaker is open. State: {HF_CIRCUIT_BREAKER.get_state()}, "
            f"failures: {HF_CIRCUIT_BREAKER.get_failure_count()}"
        )

    resolved_model = model or settings.hf_embedding_model
    resolved_provider = provider or settings.hf_inference_provider
    resolved_key = (
        api_key
        or os.environ.get("KINDLY_HF_TOKEN")
        or os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGINGFACEHUB_API_TOKEN")
    )
    resolved_dim = expected_dim or settings.embedding_dim
    resolved_timeout = timeout if timeout is not None else 30.0

    client = AsyncInferenceClient(
        provider=resolved_provider,  # type: ignore[arg-type]
        api_key=resolved_key,
        timeout=resolved_timeout,
    )

    try:
        raw = await client.feature_extraction(
            texts, model=resolved_model, normalize=True
        )  # type: ignore[arg-type]
        HF_CIRCUIT_BREAKER.record_success()
    except InferenceTimeoutError as e:
        LOGGER.error(
            f"Embedding request timed out after {resolved_timeout}s for {len(texts)} texts"
        )
        HF_CIRCUIT_BREAKER.record_failure()
        raise EmbeddingTimeoutError(
            f"Embedding request timed out after {resolved_timeout}s"
        ) from e
    except Exception as e:
        LOGGER.error(f"Embedding API request failed: {type(e).__name__}: {e}")
        HF_CIRCUIT_BREAKER.record_failure()
        raise EmbeddingAPIError(
            f"Embedding API request failed: {type(e).__name__}: {e}"
        ) from e

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
    skip_circuit_check: bool = False,
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
        skip_circuit_check=skip_circuit_check,
    )
    return vectors[0]
