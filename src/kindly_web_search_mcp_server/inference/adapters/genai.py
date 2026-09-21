"""Google GenAI provider adapter."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from ..registry import ProviderAdapter, register_provider_adapter
from ..types import LLMGeneration, ModelCapability, ModelSpec

_clients: dict[str, Any] = {}


def get_genai_client(api_key: str | None = None) -> Any:
    from google.genai import Client

    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise ValueError("GEMINI_API_KEY is missing")
    if key not in _clients:
        _clients[key] = Client(api_key=key)
    return _clients[key]


async def execute_google(
    spec: ModelSpec,
    *,
    contents: Any | None = None,
    messages: list[dict[str, str]] | None = None,
    config: Any | None = None,
    response_format: type | None = None,
    tools: list[dict[str, Any]] | None = None,
    web_search_options: dict[str, Any] | None = None,
    **kwargs: Any,
) -> LLMGeneration:
    from google.genai import types as genai_types

    from ...telemetry.usage import extract_llm_usage

    client = get_genai_client(spec.api_key)

    if contents is None and messages is not None:
        contents = "\n".join(m.get("content", "") for m in messages)

    genai_config = config or genai_types.GenerateContentConfig()
    if response_format is not None:
        genai_config.response_mime_type = "application/json"
        # The SDK serializes GenerateContentConfig with pydantic, which
        # cannot serialize a model *class* (ModelMetaclass) — passing one
        # raises PydanticSerializationError inside generate_content. The
        # synthesis path already passes a plain dict; normalize every
        # response_format to its JSON-schema dict here so all callers
        # (adaptive followup/continuation included) send a valid schema.
        schema = response_format
        model_dump = getattr(response_format, "model_json_schema", None)
        if callable(model_dump):
            schema = model_dump()
        genai_config.response_json_schema = schema

    if tools:
        for tool in tools:
            if "google_search" in tool:
                # The stub types this field as the request-side union; the tool
                # instance is the documented usage.
                genai_config.google_search = genai_types.GoogleSearch()  # ty: ignore[invalid-assignment]
            elif "url_context" in tool:
                genai_config.tools = [genai_types.Tool(url_context=genai_types.UrlContext())]

    if web_search_options:
        genai_config.google_search = genai_types.GoogleSearch()  # ty: ignore[invalid-assignment]

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=spec.model_id,
        contents=contents,
        config=genai_config,
    )

    content = response.text or ""
    if not content.strip():
        raise RuntimeError(f"{spec.provider} returned empty content")

    usage = extract_llm_usage(response)
    return LLMGeneration(
        spec=spec,
        content=content,
        usage=usage,
    )


def _init() -> None:
    register_provider_adapter(
        ProviderAdapter(
            name="google",
            execute=execute_google,
            capabilities=frozenset(
                {
                    ModelCapability.CHAT,
                    ModelCapability.STRUCTURED_OUTPUT,
                    ModelCapability.GROUNDING,
                    ModelCapability.URL_CONTEXT,
                }
            ),
        )
    )


_init()
