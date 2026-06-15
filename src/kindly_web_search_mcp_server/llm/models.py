"""Typed models for OpenAI-compatible worker routing."""

from __future__ import annotations

from dataclasses import dataclass

from .usage import LLMUsage


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
    usage: LLMUsage | None = None

    @property
    def model_used(self) -> str:
        return self.endpoint.model
