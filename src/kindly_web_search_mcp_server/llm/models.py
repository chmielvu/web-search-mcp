"""Typed models for OpenAI-compatible worker routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .usage import LLMUsage


@dataclass(frozen=True, slots=True)
class LLMEndpoint:
    """One OpenAI-compatible generation endpoint."""

    name: str
    model: str
    base_url: str
    api_key: str
    timeout_seconds: float
    client_type: Literal["openai", "huggingface"] = "openai"


@dataclass(frozen=True, slots=True)
class LLMGeneration:
    """Result returned by an endpoint attempt."""

    endpoint: LLMEndpoint
    content: str
    usage: LLMUsage | None = None
    annotations: tuple[object, ...] = ()
    provider_specific_fields: dict[str, object] | None = None

    @property
    def model_used(self) -> str:
        return self.endpoint.model

    @property
    def input_tokens(self) -> int | None:
        return self.usage.input_tokens if self.usage else None

    @property
    def output_tokens(self) -> int | None:
        return self.usage.output_tokens if self.usage else None
