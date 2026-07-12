"""Ordered LLM worker routing for classification and rewrite tasks."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

import litellm
from litellm import acompletion

from .phoenix_tracing import LLMTraceContext, openinference_context_scope
from .config import (
    build_classifier_endpoint,
    build_vercel_gpt_oss_endpoint,
    build_worker_endpoints,
)
from .models import LLMEndpoint, LLMGeneration
from .usage import extract_llm_usage

litellm.suppress_debug_info = True
litellm.set_verbose = False
litellm.turn_off_message_logging = True
warnings.filterwarnings(
    "ignore",
    message=r"Pydantic serializer warnings:.*",
    category=UserWarning,
    module=r"pydantic\.main",
)


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
        langfuse: LLMTraceContext | None = None,
        tools: list[dict[str, Any]] | None = None,
        web_search_options: dict[str, Any] | None = None,
        provider_fields: dict[str, Any] | None = None,
    ) -> LLMGeneration:
        errors: list[Exception] = []
        for endpoint in self.endpoints:
            try:
                request_kwargs: dict[str, Any] = {
                    "model": endpoint.litellm_model,
                    "messages": messages,
                    "temperature": temperature,
                    "api_base": endpoint.base_url,
                    "api_key": endpoint.api_key,
                    "timeout": timeout_seconds or endpoint.timeout_seconds,
                    "no-log": True,
                }
                if response_format is not None:
                    request_kwargs["response_format"] = response_format
                if tools is not None:
                    request_kwargs["tools"] = tools
                if web_search_options is not None:
                    request_kwargs["web_search_options"] = web_search_options
                if provider_fields:
                    request_kwargs.update(provider_fields)
                if reasoning_effort is not None and endpoint.name not in {
                    "groq",
                    "cerebras",
                    "vercel",
                }:
                    request_kwargs["reasoning_effort"] = reasoning_effort
                with openinference_context_scope(langfuse):
                    response = await acompletion(**request_kwargs)
                message = response.choices[0].message
                content = message.content or ""  # type: ignore[union-attr]
                annotations = tuple(getattr(message, "annotations", None) or ())
                provider_specific_fields = (
                    getattr(message, "provider_specific_fields", None) or None
                )
                if content.strip() or annotations or provider_specific_fields:
                    return LLMGeneration(
                        endpoint=endpoint,
                        content=content,
                        usage=extract_llm_usage(response),
                        annotations=annotations,
                        provider_specific_fields=provider_specific_fields,
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
        langfuse: LLMTraceContext | None = None,
        tools: list[dict[str, Any]] | None = None,
        web_search_options: dict[str, Any] | None = None,
        provider_fields: dict[str, Any] | None = None,
    ) -> LLMGeneration:
        return await self._complete(
            messages=messages,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            response_format=response_model or {"type": "json_object"},
            reasoning_effort=reasoning_effort,
            langfuse=langfuse,
            tools=tools,
            web_search_options=web_search_options,
            provider_fields=provider_fields,
        )

    async def complete_text(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        timeout_seconds: float | None = None,
        reasoning_effort: str | None = None,
        langfuse: LLMTraceContext | None = None,
        tools: list[dict[str, Any]] | None = None,
        web_search_options: dict[str, Any] | None = None,
        provider_fields: dict[str, Any] | None = None,
    ) -> LLMGeneration:
        return await self._complete(
            messages=messages,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            response_format=None,
            reasoning_effort=reasoning_effort,
            langfuse=langfuse,
            tools=tools,
            web_search_options=web_search_options,
            provider_fields=provider_fields,
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
