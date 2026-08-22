"""Hugging Face Inference Provider embeddings."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import threading
import time
from typing import Any

from huggingface_hub import AsyncInferenceClient, InferenceTimeoutError

from ..settings import settings

EMBEDDING_DIM = 786
LOGGER = logging.getLogger(__name__)

# E5-instruct requires a task instruction prefix for queries, NOT for passages.
# https://huggingface.co/intfloat/multilingual-e5-large-instruct#usage
_E5_INSTRUCT_TASK = "Given a web search query, retrieve relevant passages that answer the query"


def _format_query_with_instruction(query: str) -> str:
    """Wrap a query with the E5-instruct task prefix."""
    return f"Instruct: {_E5_INSTRUCT_TASK}\nQuery: {query}"


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
        self._half_open_probe_claimed = False
        self._lock = threading.Lock()

    def is_open(self) -> bool:
        """Check if circuit is open (calls should be blocked)."""
        with self._lock:
            if self._state == "closed":
                return False
            if self._state == "open":
                elapsed = time.time() - self._last_failure_time
                if elapsed < self.RECOVERY_TIMEOUT_SECONDS:
                    return True
                LOGGER.info("Circuit breaker entering HALF_OPEN state after recovery timeout")
                self._state = "half_open"
                self._half_open_success = False
                self._half_open_probe_claimed = False
            if self._state == "half_open":
                if self._half_open_probe_claimed:
                    return True
                self._half_open_probe_claimed = True
                return False
            return True

    def reset(self) -> None:
        """Explicitly reset circuit breaker to closed state."""
        with self._lock:
            self._failure_count = 0
            self._last_failure_time = 0.0
            self._state = "closed"
            self._half_open_success = False
            self._half_open_probe_claimed = False

    def record_success(self) -> None:
        """Record successful call, reset circuit."""
        self.reset()
    def record_failure(self) -> None:
        """Record failed call, potentially open circuit."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            self._half_open_probe_claimed = False
            if self._state == "half_open":
                LOGGER.warning("Circuit breaker test call failed, returning to OPEN")
                self._state = "open"
                return
            if self._failure_count >= self.FAILURE_THRESHOLD:
                LOGGER.warning(
                    "Circuit breaker OPENED after %s consecutive failures. Will auto-recover in %ss",
                    self._failure_count,
                    self.RECOVERY_TIMEOUT_SECONDS,
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



# Singleton AsyncInferenceClient for connection reuse across embedding calls.
# The HF library lazily creates an internal httpx.AsyncClient; reusing the
# same AsyncInferenceClient instance gives us TCP/TLS connection pooling.
_HF_CLIENT: AsyncInferenceClient | None = None
_HF_CLIENT_LOCK = asyncio.Lock()


async def _get_hf_client(
    provider: str | None = None,
    api_key: str | None = None,
    timeout: float | None = None,
) -> AsyncInferenceClient:
    """Return a singleton AsyncInferenceClient, creating it if needed."""
    global _HF_CLIENT
    if _HF_CLIENT is not None:
        return _HF_CLIENT

    async with _HF_CLIENT_LOCK:
        if _HF_CLIENT is not None:
            return _HF_CLIENT

        resolved_provider = provider or settings.hf_inference_provider
        resolved_key = (
            api_key or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACEHUB_API_TOKEN")
        )
        # The singleton's internal HTTP timeout must be the global maximum so that
        # per-call deadlines (enforced by asyncio.wait_for in embed_texts) are the
        # only timeout that matters. Passing per-call timeouts here would pin the
        # client to whatever the first caller requested, causing later calls with
        # longer deadlines to still hit the first caller's shorter internal timeout.
        resolved_timeout = settings.embedding_timeout_seconds

        client_kwargs: dict[str, Any] = {
            "api_key": resolved_key,
            "timeout": resolved_timeout,
        }
        try:
            sig = inspect.signature(AsyncInferenceClient.__init__)
            if "provider" in sig.parameters and resolved_provider is not None:
                client_kwargs["provider"] = resolved_provider
        except (ValueError, TypeError):
            pass

        try:
            _HF_CLIENT = AsyncInferenceClient(**client_kwargs)
        except TypeError as exc:
            if "provider" in client_kwargs and "unexpected keyword argument 'provider'" in str(exc):
                client_kwargs.pop("provider", None)
                _HF_CLIENT = AsyncInferenceClient(**client_kwargs)
            else:
                raise
        LOGGER.info(
            "Created singleton AsyncInferenceClient (provider=%s, timeout=%.1fs)",
            resolved_provider if "provider" in client_kwargs else None,
            resolved_timeout,
        )
        return _HF_CLIENT


async def reset_hf_client() -> None:
    """Close and clear the shared HF client after cancellation or connection issues."""
    global _HF_CLIENT
    async with _HF_CLIENT_LOCK:
        client = _HF_CLIENT
        _HF_CLIENT = None
    if client is None:
        return
    close = getattr(client, "close", None)
    if close is None:
        return
    close_result = close()
    if inspect.isawaitable(close_result):
        await close_result


async def embed_texts(
    texts: list[str],
    *,
    model: str | None = None,
    provider: str | None = None,
    api_key: str | None = None,
    expected_dim: int | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
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
    resolved_dim = expected_dim or settings.embedding_dim
    resolved_timeout = timeout if timeout is not None else settings.embedding_timeout_seconds

    # Use singleton client for connection reuse. The singleton is created with the
    # global embedding timeout so per-call timeout overrides only affect the outer
    # asyncio.wait_for deadline, not the client's internal HTTP timeout.
    client = await _get_hf_client(provider=provider, api_key=api_key)

    max_retries = max_retries if max_retries is not None else settings.embedding_max_retries
    retry_delay = settings.embedding_retry_delay_seconds
    raw = None

    for attempt in range(max_retries + 1):
        try:
            raw = await asyncio.wait_for(
                client.feature_extraction(  # type: ignore[arg-type]
                    texts,
                    model=resolved_model,
                    normalize=True,  # type: ignore[arg-type]
                ),
                timeout=resolved_timeout,
            )
            HF_CIRCUIT_BREAKER.record_success()
            break
        except (asyncio.TimeoutError, InferenceTimeoutError) as e:
            HF_CIRCUIT_BREAKER.record_failure()
            if attempt < max_retries:
                LOGGER.warning(
                    "Embedding timeout attempt %d/%d, retrying in %.1fs",
                    attempt + 1,
                    max_retries + 1,
                    retry_delay,
                )
                await asyncio.sleep(retry_delay)  # type: ignore[name-defined]
            else:
                LOGGER.error(
                    "Embedding timed out after %d attempts",
                    max_retries + 1,
                )
                raise EmbeddingTimeoutError(
                    f"Embedding request timed out ({max_retries + 1} attempts)"
                ) from e
        except Exception as e:
            LOGGER.error(f"Embedding API request failed: {type(e).__name__}: {e}")
            HF_CIRCUIT_BREAKER.record_failure()
            raise EmbeddingAPIError(f"Embedding API request failed: {type(e).__name__}: {e}") from e

    assert raw is not None  # guaranteed by the loop above
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
    """Embed a single query through Hugging Face Inference Providers.

    For E5-instruct models, the query is automatically wrapped with a task
    instruction prefix per the model card guidelines.
    """
    vectors = await embed_texts(
        [_format_query_with_instruction(query)],
        model=model,
        provider=provider,
        api_key=api_key,
        expected_dim=expected_dim,
        timeout=timeout,
        http_client=http_client,
        skip_circuit_check=skip_circuit_check,
    )
    return vectors[0]
