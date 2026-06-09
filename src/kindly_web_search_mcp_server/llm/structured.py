"""Structured LLM task helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class StructuredLLMRequest:
    task: str
    messages: list[dict[str, str]]
    temperature: float = 0.0
    timeout_seconds: float | None = None
    response_schema: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class StructuredLLMResponse:
    endpoint_name: str
    model_name: str
    content: str
