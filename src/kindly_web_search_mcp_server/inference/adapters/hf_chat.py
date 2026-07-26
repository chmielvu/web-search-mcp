"""Hugging Face Inference Client provider adapter."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel

from ..registry import ProviderAdapter, register_provider_adapter
from ..types import LLMGeneration, ModelCapability, ModelSpec
from ...telemetry.phoenix_tracing import LLMTraceContext, openinference_context_scope
from ...telemetry.usage import extract_llm_usage


async def execute_hf_chat(
    spec: ModelSpec,
    *,
    messages: list[dict[str, str]],
    temperature: float = 0.0,
    timeout_seconds: float | None = None,
    response_format: Any | None = None,
    reasoning_effort: str | None = None,
    langfuse: LLMTraceContext | None = None,
    **kwargs: Any,
) -> LLMGeneration:
    from huggingface_hub import InferenceClient

    request_kwargs: dict[str, Any] = {
        "model": spec.model_id,
        "messages": messages,
        "temperature": temperature,
    }
    effective_timeout = timeout_seconds or spec.default_timeout

    if isinstance(response_format, type) and issubclass(response_format, BaseModel):
        request_kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": response_format.__name__,
                "schema": response_format.model_json_schema(),
                "strict": True,
            },
        }
    elif response_format is not None:
        request_kwargs["response_format"] = response_format

    with openinference_context_scope(langfuse):
        client = InferenceClient(api_key=spec.api_key, timeout=effective_timeout)
        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.chat.completions.create,
                **request_kwargs,
            ),
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
            name="huggingface",
            execute=execute_hf_chat,
            capabilities=frozenset({ModelCapability.CHAT, ModelCapability.STRUCTURED_OUTPUT}),
        )
    )


_init()
