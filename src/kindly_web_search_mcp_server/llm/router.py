"""Ordered OpenAI-compatible routing for classification and rewrite tasks."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from huggingface_hub import InferenceClient
from openai import AsyncOpenAI
from pydantic import BaseModel

from .phoenix_tracing import LLMTraceContext, openinference_context_scope
from .config import (
    build_classifier_endpoint,
    build_vercel_gpt_oss_endpoint,
    build_worker_endpoints,
)
from .models import LLMEndpoint, LLMGeneration
from .usage import extract_llm_usage


def _huggingface_completion(
    endpoint: LLMEndpoint,
    request_kwargs: dict[str, Any],
    response_format: Any | None,
    timeout_seconds: float,
) -> Any:
    """Run one synchronous Hugging Face request in a worker thread."""
    huggingface_kwargs = dict(request_kwargs)
    if isinstance(response_format, type) and issubclass(response_format, BaseModel):
        huggingface_kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": response_format.__name__,
                "schema": response_format.model_json_schema(),
                "strict": True,
            },
        }
    elif response_format is not None:
        huggingface_kwargs["response_format"] = response_format

    client = InferenceClient(api_key=endpoint.api_key, timeout=timeout_seconds)
    return client.chat.completions.create(**huggingface_kwargs)

@dataclass(frozen=True, slots=True)
class LLMRouter:
    """Sequential router across OpenAI-compatible endpoints."""

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
                    "model": endpoint.model,
                    "messages": messages,
                    "temperature": temperature,
                }
                if tools is not None:
                    request_kwargs["tools"] = tools
                if web_search_options is not None:
                    request_kwargs["web_search_options"] = web_search_options
                if provider_fields:
                    request_kwargs["extra_body"] = provider_fields
                if reasoning_effort is not None and endpoint.name not in {
                    "groq",
                    "cerebras",
                    "huggingface",
                    "vercel",
                }:
                    request_kwargs["reasoning_effort"] = reasoning_effort
                effective_timeout = timeout_seconds or endpoint.timeout_seconds
                with openinference_context_scope(langfuse):
                    if endpoint.client_type == "huggingface":
                        response = await asyncio.wait_for(
                            asyncio.to_thread(
                                _huggingface_completion,
                                endpoint,
                                request_kwargs,
                                response_format,
                                effective_timeout,
                            ),
                            timeout=effective_timeout + 5.0,
                        )
                    else:
                        async with AsyncOpenAI(
                            api_key=endpoint.api_key,
                            base_url=endpoint.base_url,
                            timeout=effective_timeout,
                            max_retries=0,
                        ) as client:
                            if isinstance(response_format, type) and issubclass(
                                response_format, BaseModel
                            ):
                                response = await asyncio.wait_for(
                                    client.chat.completions.parse(
                                        **request_kwargs,
                                        response_format=response_format,
                                    ),
                                    timeout=effective_timeout + 5.0,
                                )
                            else:
                                if response_format is not None:
                                    request_kwargs["response_format"] = response_format
                                response = await asyncio.wait_for(
                                    client.chat.completions.create(**request_kwargs),
                                    timeout=effective_timeout + 5.0,
                                )
                message = response.choices[0].message
                parsed = getattr(message, "parsed", None)
                content = (
                    parsed.model_dump_json()
                    if isinstance(parsed, BaseModel)
                    else (message.content or "")
                )
                annotations = tuple(getattr(message, "annotations", None) or ())
                provider_specific_fields = (
                    getattr(message, "provider_specific_fields", None)
                    or getattr(message, "model_extra", None)
                    or None
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

    async def complete_text_messages(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        timeout_seconds: float | None = None,
        reasoning_effort: str | None = None,
        langfuse: LLMTraceContext | None = None,
    ) -> LLMGeneration:
        return await self._complete(
            messages=messages,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            reasoning_effort=reasoning_effort,
            langfuse=langfuse,
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
        langfuse: LLMTraceContext | None = None,
        tools: list[dict[str, Any]] | None = None,
        web_search_options: dict[str, Any] | None = None,
        provider_fields: dict[str, Any] | None = None,
    ) -> LLMGeneration:
        return await self._complete(
            messages=messages,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            reasoning_effort=reasoning_effort,
            langfuse=langfuse,
            tools=tools,
            web_search_options=web_search_options,
            provider_fields=provider_fields,
        )


def build_classifier_router() -> LLMRouter:
    return LLMRouter(
        endpoints=(
            build_classifier_endpoint(),
            build_vercel_gpt_oss_endpoint(timeout_seconds=20.0),
        )
    )


def build_worker_router() -> LLMRouter:
    """Worker ladder: Cerebras -> Groq -> Hugging Face/Nscale -> Vercel."""
    return LLMRouter(build_worker_endpoints())
