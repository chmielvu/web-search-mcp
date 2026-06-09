"""Ordered LLM worker routing for classification and rewrite tasks."""

from __future__ import annotations

from dataclasses import dataclass

from .client import build_client
from .config import build_classifier_endpoint, build_worker_endpoints
from .models import LLMEndpoint, LLMGeneration


@dataclass(frozen=True, slots=True)
class LLMRouter:
    """Sequential fallback router across OpenAI-compatible endpoints."""

    endpoints: tuple[LLMEndpoint, ...]

    async def complete_json(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        timeout_seconds: float | None = None,
    ) -> LLMGeneration:
        errors: list[Exception] = []
        for endpoint in self.endpoints:
            client = build_client(endpoint)
            try:
                response = await client.chat.completions.create(
                    model=endpoint.model,
                    messages=messages,
                    temperature=temperature,
                    response_format={"type": "json_object"},
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
    """Classifier prefers Vercel AI Gateway, then falls back to the worker ladder."""
    return LLMRouter((build_classifier_endpoint(), *build_worker_endpoints()))


def build_worker_router() -> LLMRouter:
    """Worker ladder: Cerebras GPT-OSS 120B → Groq GPT-OSS 120B → Vercel Groq GPT-OSS-20B."""
    return LLMRouter(build_worker_endpoints())
