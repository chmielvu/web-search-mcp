"""Shared inference type contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


_API_KEY_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "SECOND_GEMINI_API_KEY": ("GEMINI_SECOND_API_KEY",),
}


class ModelCapability(str, Enum):
    CHAT = "chat"
    STRUCTURED_OUTPUT = "structured_output"
    GROUNDING = "grounding"
    URL_CONTEXT = "url_context"
    RERANK = "rerank"
    EMBEDDING = "embedding"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    spec_id: str
    provider: str
    model_id: str
    base_url: str | None
    api_key_env: str
    capabilities: frozenset[ModelCapability]
    cost_per_1m_input: float | None = None
    cost_per_1m_output: float | None = None
    default_timeout: float = 30.0
    source: str = "catalog"

    @property
    def api_key(self) -> str:
        import os

        env_names = (self.api_key_env, *_API_KEY_ENV_ALIASES.get(self.api_key_env, ()))
        for env_name in env_names:
            value = os.environ.get(env_name, "").strip()
            if value:
                return value
        return ""


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


@dataclass(frozen=True, slots=True)
class LLMGeneration:
    """Result returned by an endpoint attempt."""

    spec: ModelSpec
    content: str
    usage: LLMUsage | None = None
    annotations: tuple[object, ...] = ()
    provider_specific_fields: dict[str, object] | None = None

    @property
    def model_used(self) -> str:
        return self.spec.model_id

    @property
    def input_tokens(self) -> int | None:
        return self.usage.input_tokens if self.usage else None

    @property
    def output_tokens(self) -> int | None:
        return self.usage.output_tokens if self.usage else None
