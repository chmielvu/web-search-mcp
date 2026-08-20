"""Embedding adapter wrapping Unified ML and HF inference providers."""

from __future__ import annotations

from ...embeddings import embed_query, embed_texts
from ..types import ModelSpec


async def embed_texts_with_spec(
    spec: ModelSpec,
    texts: list[str],
    *,
    timeout: float | None = None,
    max_retries: int | None = None,
    skip_circuit_check: bool = False,
) -> list[list[float]]:
    """Embed texts using the unified inference ModelSpec configuration."""
    return await embed_texts(
        texts,
        provider=spec.provider if spec.provider != "huggingface" else "hf-inference",
        api_key=spec.api_key or None,
        model=spec.model_id,
        timeout=timeout or spec.default_timeout,
        max_retries=max_retries,
        skip_circuit_check=skip_circuit_check,
    )


async def embed_query_with_spec(
    spec: ModelSpec,
    query: str,
    *,
    timeout: float | None = None,
    skip_circuit_check: bool = False,
) -> list[float]:
    """Embed a single query using the unified inference ModelSpec configuration."""
    return await embed_query(
        query,
        provider=spec.provider if spec.provider != "huggingface" else "hf-inference",
        api_key=spec.api_key or None,
        model=spec.model_id,
        timeout=timeout or spec.default_timeout,
        skip_circuit_check=skip_circuit_check,
    )
