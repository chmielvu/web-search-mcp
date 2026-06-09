"""Typed models for OpenAI-compatible worker routing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LLMEndpoint:
    """One OpenAI-compatible generation endpoint."""

    name: str
    model: str
    base_url: str
    api_key: str
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class LLMGeneration:
    """Result returned by an endpoint attempt."""

    endpoint: LLMEndpoint
    content: str
