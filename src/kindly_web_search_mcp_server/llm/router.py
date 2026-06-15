"""Ordered LLM worker routing for classification and rewrite tasks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from litellm import acompletion

from .langfuse_tracing import (
    LangfuseTraceContext,
    build_langfuse_litellm_kwargs,
    ensure_langfuse_litellm_callbacks,
)
from .config import (
    build_classifier_endpoint,
    build_vercel_gpt_oss_endpoint,
    build_worker_endpoints,
)
from .models import LLMEndpoint, LLMGeneration
from .usage import extract_llm_usage


@dataclass(frozen=True, slots=True)
class LLMRouter:
    """Sequential LiteLLM router across configured endpoints."""

    endpoints: tuple[LLMEndpoint, ...]

    async def _complete(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        timeout_seconds: float | None = None,
        response_format: Any | None = None,
        reasoning_effort: str | None = None,
        langfuse: LangfuseTraceContext | None = None,
    ) -> LLMGeneration:
        ensure_langfuse_litellm_callbacks()
        errors: list[Exception] = []
        for endpoint in self.endpoints:
            try:
                request_kwargs: dict[str, Any] = {
                    "model": endpoint.model,
                    "messages": messages,
                    "temperature": temperature,
                    "api_base": endpoint.base_url,
                    "api_key": endpoint.api_key,
                    "timeout": timeout_seconds or endpoint.timeout_seconds,
                }
                if response_format is not None:
                    request_kwargs["response_format"] = response_format
                if reasoning_effort is not None:
                    request_kwargs["reasoning_effort"] = reasoning_effort
                request_kwargs.update(
                    build_langfuse_litellm_kwargs(
                        generation_name=f"{endpoint.name}:{endpoint.model}",
                        trace_context=langfuse,
                    )
                )
                response = await acompletion(**request_kwargs)
                content = response.choices[0].message.content or ""
                if content.strip():
                    return LLMGeneration(
                        endpoint=endpoint,
                        content=content,
                        usage=extract_llm_usage(response),
                    )
                raise RuntimeError(f"{endpoint.name} returned empty content")
            except Exception as exc:  # sequential provider ladder, no hidden fallback
                errors.append(exc)
        raise RuntimeError(
            "All LLM endpoints failed: "
            + "; ".join(f"{type(error).__name__}: {error}" for error in errors)
        )

    async def complete_json(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        timeout_seconds: float | None = None,
        response_model: type[Any] | None = None,
        reasoning_effort: str | None = None,
        langfuse: LangfuseTraceContext | None = None,
    ) -> LLMGeneration:
        return await self._complete(
            messages=messages,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            response_format=response_model or {"type": "json_object"},
            reasoning_effort=reasoning_effort,
            langfuse=langfuse,
        )

    async def complete_text(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        timeout_seconds: float | None = None,
        reasoning_effort: str | None = None,
        langfuse: LangfuseTraceContext | None = None,
    ) -> LLMGeneration:
        return await self._complete(
            messages=messages,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            response_format=None,
            reasoning_effort=reasoning_effort,
            langfuse=langfuse,
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
