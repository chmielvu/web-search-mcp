"""Ordered LLM worker routing for classification and rewrite tasks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from litellm import acompletion

from .config import (
    build_classifier_endpoint,
    build_vercel_gpt_oss_endpoint,
    build_worker_endpoints,
)
from .models import LLMEndpoint, LLMGeneration


@dataclass(frozen=True, slots=True)
class LLMRouter:
    """Sequential LiteLLM router across configured endpoints."""

    endpoints: tuple[LLMEndpoint, ...]

    async def complete_json(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        timeout_seconds: float | None = None,
        response_model: type[Any] | None = None,
    ) -> LLMGeneration:
        errors: list[Exception] = []
        for endpoint in self.endpoints:
            try:
                response = await acompletion(
                    model=endpoint.model,
                    messages=messages,
                    temperature=temperature,
                    response_format=response_model or {"type": "json_object"},
                    api_base=endpoint.base_url,
                    api_key=endpoint.api_key,
                    timeout=timeout_seconds or endpoint.timeout_seconds,
                )
                content = response.choices[0].message.content or ""
                if content.strip():
                    return LLMGeneration(endpoint=endpoint, content=content)
                raise RuntimeError(f"{endpoint.name} returned empty content")
            except Exception as exc:  # sequential provider ladder, no hidden fallback
                errors.append(exc)
        raise RuntimeError(
            "All LLM endpoints failed: "
            + "; ".join(f"{type(error).__name__}: {error}" for error in errors)
        )


def build_classifier_router() -> LLMRouter:
    """Classifier prefers Groq GPT-OSS 20B, then falls back to Vercel GPT-OSS."""
    return LLMRouter(
        (
            build_classifier_endpoint(),
            build_vercel_gpt_oss_endpoint(timeout_seconds=20.0),
        )
    )


def build_worker_router() -> LLMRouter:
    """Worker ladder: Cerebras GPT-OSS 120B → Groq GPT-OSS 120B → Vercel Groq GPT-OSS-20B."""
    return LLMRouter(build_worker_endpoints())
