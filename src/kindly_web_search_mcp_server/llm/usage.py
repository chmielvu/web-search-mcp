"""Helpers for extracting and packaging LLM usage metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LLMUsage:
    """Token usage extracted from an LLM response."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    @property
    def has_values(self) -> bool:
        return any(
            value is not None
            for value in (self.input_tokens, self.output_tokens, self.total_tokens)
        )


def _as_mapping(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dumped if isinstance(dumped, dict) else None
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return None


def _first_int(mapping: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
    return None


def _usage_source(source: Any) -> Any | None:
    for name in ("usage", "usage_metadata"):
        nested = getattr(source, name, None)
        if nested is not None:
            return nested

    mapping = _as_mapping(source)
    if mapping is not None:
        nested = mapping.get("usage")
        if nested is not None:
            return nested
        nested = mapping.get("usage_metadata")
        if nested is not None:
            return nested
        if any(
            key in mapping
            for key in (
                "input_tokens",
                "prompt_tokens",
                "prompt_token_count",
                "output_tokens",
                "completion_tokens",
                "response_token_count",
                "total_tokens",
                "total_token_count",
            )
        ):
            return mapping
    return None


def extract_llm_usage(source: Any) -> LLMUsage | None:
    """Extract token usage from a response object or response payload."""

    usage_source = _usage_source(source)
    if usage_source is None:
        return None

    mapping = _as_mapping(usage_source)
    if mapping is None:
        return None

    input_tokens = _first_int(
        mapping,
        ("input_tokens", "prompt_tokens", "prompt_token_count"),
    )
    output_tokens = _first_int(
        mapping,
        ("output_tokens", "completion_tokens", "response_token_count"),
    )
    total_tokens = _first_int(mapping, ("total_tokens", "total_token_count"))

    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None

    return LLMUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def llm_usage_fields(*, model_used: str | None, usage: LLMUsage | None) -> dict[str, int | str]:
    """Build a compact analytics payload for model and token usage."""

    fields: dict[str, int | str] = {}
    if model_used:
        fields["model_used"] = model_used
    if usage is None:
        return fields
    if usage.input_tokens is not None:
        fields["input_tokens"] = usage.input_tokens
    if usage.output_tokens is not None:
        fields["output_tokens"] = usage.output_tokens
    return fields
