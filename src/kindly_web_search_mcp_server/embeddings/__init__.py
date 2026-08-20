"""Embeddings module for web-search-mcp.

Unified ML ONNX service (port 8000) is the primary embeddings path, with
Hugging Face Inference Provider available as an alternate/fallback provider.
"""

from __future__ import annotations

import logging
from typing import Any

from ..settings import settings
from .hf_inference import (
    CircuitOpenError,
    EmbeddingAPIError,
    EmbeddingDimensionError,
    EmbeddingTimeoutError,
    reset_hf_client,
)
from .hf_inference import embed_query as _hf_embed_query
from .hf_inference import embed_texts as _hf_embed_texts
from .unified_ml import embed_query as _uml_embed_query
from .unified_ml import embed_texts as _uml_embed_texts
from .unified_ml import reset_unifiedml_client

LOGGER = logging.getLogger(__name__)
EMBEDDING_DIM = 384  # Default dimension for multilingual-e5-small


async def embed_texts(
    texts: list[str],
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    expected_dim: int | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
    http_client: Any = None,
    skip_circuit_check: bool = False,
) -> list[list[float]]:
    """Embed texts using the configured primary embedding provider.

    Defaults to Unified ML (port 8000). If provider is set to 'hf-inference' or 'huggingface',
    routes to Hugging Face Inference API.
    """
    resolved_provider = (provider or settings.embedding_provider).lower()

    if resolved_provider in ("hf-inference", "huggingface", "hf"):
        return await _hf_embed_texts(
            texts,
            model=model,
            provider=provider if provider not in ("hf-inference", "huggingface", "hf") else None,
            api_key=api_key,
            expected_dim=expected_dim,
            timeout=timeout,
            max_retries=max_retries,
            http_client=http_client,
            skip_circuit_check=skip_circuit_check,
        )

    # Primary path: Unified ML service
    try:
        return await _uml_embed_texts(
            texts,
            model=model,
            base_url=base_url,
            expected_dim=expected_dim,
            timeout=timeout,
            max_retries=max_retries,
            http_client=http_client,
            skip_circuit_check=skip_circuit_check,
        )
    except (EmbeddingAPIError, EmbeddingTimeoutError, CircuitOpenError) as err:
        # If HF_TOKEN is configured and Unified ML fails, attempt HF fallback
        if settings.hf_token and resolved_provider not in ("unifiedml_only",):
            LOGGER.warning(
                "Unified ML embedding failed (%s: %s), falling back to HF Inference",
                type(err).__name__,
                err,
            )
            return await _hf_embed_texts(
                texts,
                model=model,
                api_key=api_key,
                expected_dim=expected_dim,
                timeout=timeout,
                max_retries=max_retries,
                http_client=http_client,
                skip_circuit_check=skip_circuit_check,
            )
        raise


async def embed_query(
    query: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    expected_dim: int | None = None,
    timeout: float | None = None,
    http_client: Any = None,
    skip_circuit_check: bool = False,
) -> list[float]:
    """Embed a single query using the configured primary embedding provider."""
    resolved_provider = (provider or settings.embedding_provider).lower()

    if resolved_provider in ("hf-inference", "huggingface", "hf"):
        return await _hf_embed_query(
            query,
            model=model,
            provider=provider if provider not in ("hf-inference", "huggingface", "hf") else None,
            api_key=api_key,
            expected_dim=expected_dim,
            timeout=timeout,
            http_client=http_client,
            skip_circuit_check=skip_circuit_check,
        )

    # Primary path: Unified ML service
    try:
        return await _uml_embed_query(
            query,
            model=model,
            base_url=base_url,
            expected_dim=expected_dim,
            timeout=timeout,
            http_client=http_client,
            skip_circuit_check=skip_circuit_check,
        )
    except (EmbeddingAPIError, EmbeddingTimeoutError, CircuitOpenError) as err:
        # If HF_TOKEN is configured and Unified ML fails, attempt HF fallback
        if settings.hf_token and resolved_provider not in ("unifiedml_only",):
            LOGGER.warning(
                "Unified ML embedding failed (%s: %s), falling back to HF Inference",
                type(err).__name__,
                err,
            )
            return await _hf_embed_query(
                query,
                model=model,
                api_key=api_key,
                expected_dim=expected_dim,
                timeout=timeout,
                http_client=http_client,
                skip_circuit_check=skip_circuit_check,
            )
        raise


async def reset_embedding_clients() -> None:
    """Reset both Unified ML and HF client connections."""
    await reset_unifiedml_client()
    await reset_hf_client()


def __getattr__(name: str) -> Any:
    if name == "EMBEDDING_DIM":
        return settings.embedding_dim
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "embed_texts",
    "embed_query",
    "EMBEDDING_DIM",
    "EmbeddingDimensionError",
    "EmbeddingTimeoutError",
    "EmbeddingAPIError",
    "CircuitOpenError",
    "reset_embedding_clients",
    "reset_unifiedml_client",
    "reset_hf_client",
]
