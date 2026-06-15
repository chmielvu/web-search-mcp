"""Structured LLM task helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .phoenix_tracing import LLMTraceContext
from .usage import LLMUsage


@dataclass(frozen=True, slots=True)
class StructuredLLMRequest:
    task: str
    messages: list[dict[str, str]]
    temperature: float = 0.0
    timeout_seconds: float | None = None
    response_model: type[Any] | None = None
    reasoning_effort: str | None = None
    langfuse: LLMTraceContext | None = None


@dataclass(frozen=True, slots=True)
class StructuredLLMResponse:
    endpoint_name: str
    model_name: str
    content: str
    usage: LLMUsage | None = None

    @property
    def model_used(self) -> str:
        return self.model_name

    @property
    def input_tokens(self) -> int | None:
        return self.usage.input_tokens if self.usage else None

    @property
    def output_tokens(self) -> int | None:
        return self.usage.output_tokens if self.usage else None
