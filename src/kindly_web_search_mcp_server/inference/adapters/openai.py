"""OpenAI-compatible provider adapter (cerebras, groq, vercel, openrouter)."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel

from ..registry import ProviderAdapter, register_provider_adapter
from ..types import LLMGeneration, ModelCapability, ModelSpec
from ...telemetry.phoenix_tracing import LLMTraceContext, openinference_context_scope
from ...telemetry.usage import extract_llm_usage


_CEREBRAS_NON_HARMONY_MODELS = frozenset({"zai-glm-4.7", "gemma-4-31b"})


def _adapt_cerebras_messages(
    spec: ModelSpec, messages: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Remove GPT-OSS Harmony reasoning directives for other Cerebras models."""
    if spec.provider != "cerebras" or spec.model_id not in _CEREBRAS_NON_HARMONY_MODELS:
        return messages

    adapted: list[dict[str, str]] = []
    for message in messages:
        if message.get("role") != "system":
            adapted.append(message)
            continue
        content = message.get("content", "")
        if not content or not any(
            line.lstrip().casefold().startswith("reasoning:") for line in content.splitlines()
        ):
            adapted.append(message)
            continue
        adapted.append(
            {
                **message,
                "content": "\n".join(
                    line
                    for line in content.splitlines()
                    if not line.lstrip().casefold().startswith("reasoning:")
                ).strip(),
            }
        )
    return adapted


async def execute_openai(
    spec: ModelSpec,
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
    **kwargs: Any,
) -> LLMGeneration:
    from openai import AsyncOpenAI

    request_kwargs: dict[str, Any] = {
        "model": spec.model_id,
        "messages": _adapt_cerebras_messages(spec, messages),
        "temperature": temperature,
    }
    if tools is not None:
        request_kwargs["tools"] = tools
    if web_search_options is not None:
        request_kwargs["web_search_options"] = web_search_options
    if provider_fields:
        request_kwargs["extra_body"] = provider_fields
    if reasoning_effort is not None and (
        spec.provider not in {"groq", "cerebras", "huggingface", "vercel"}
        or (spec.provider == "cerebras" and spec.model_id in _CEREBRAS_NON_HARMONY_MODELS)
    ):
        request_kwargs["reasoning_effort"] = reasoning_effort

    effective_timeout = timeout_seconds or spec.default_timeout

    with openinference_context_scope(langfuse):
        async with AsyncOpenAI(
            api_key=spec.api_key,
            base_url=spec.base_url,
            timeout=effective_timeout,
            max_retries=0,
        ) as client:
            if isinstance(response_format, type) and issubclass(response_format, BaseModel):
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
    content = parsed.model_dump_json() if isinstance(parsed, BaseModel) else (message.content or "")
    annotations = tuple(getattr(message, "annotations", None) or ())
    provider_specific_fields = (
        getattr(message, "provider_specific_fields", None)
        or getattr(message, "model_extra", None)
        or None
    )

    if not (content.strip() or annotations or provider_specific_fields):
        raise RuntimeError(f"{spec.provider} returned empty content")

    usage = extract_llm_usage(response)
    return LLMGeneration(
        spec=spec,
        content=content,
        usage=usage,
        annotations=annotations,
        provider_specific_fields=provider_specific_fields,
    )


def _init() -> None:
    register_provider_adapter(
        ProviderAdapter(
            name="openai",
            execute=execute_openai,
            capabilities=frozenset({ModelCapability.CHAT, ModelCapability.STRUCTURED_OUTPUT}),
        )
    )
    from ..registry import register_provider_alias

    for alias in ("cerebras", "groq", "vercel", "openrouter"):
        register_provider_alias(alias, "openai")


_init()
