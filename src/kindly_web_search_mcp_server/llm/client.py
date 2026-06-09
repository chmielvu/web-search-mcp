"""Small OpenAI-compatible client helpers."""

from __future__ import annotations

from openai import AsyncOpenAI

from .models import LLMEndpoint


def build_client(endpoint: LLMEndpoint) -> AsyncOpenAI:
    """Build an OpenAI-compatible client for one endpoint."""
    return AsyncOpenAI(
        api_key=endpoint.api_key,
        base_url=endpoint.base_url,
        timeout=endpoint.timeout_seconds,
        max_retries=0,
    )
