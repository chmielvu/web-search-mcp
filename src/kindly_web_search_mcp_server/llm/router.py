"""Ordered OpenAI-compatible routing for classification and rewrite tasks."""

from __future__ import annotations

import asyncio
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from huggingface_hub import InferenceClient
from openai import AsyncOpenAI

# Pre-import openai.resources.chat to avoid first-request lazy-import lock
# contention. openai.AsyncOpenAI.chat is a lazy @property; its first access
# imports openai.resources.chat, which contends with other lazy imports
# (e.g., keyword extraction's nltk/scipy) for the Python global import lock.
# Pre-loading at module level ensures the import happens at server startup,
# not during the first stdio tool call, where it blocks the event loop.
import openai.resources.chat as _openai_chat  # noqa: F401
from pydantic import BaseModel

from .phoenix_tracing import LLMTraceContext, openinference_context_scope
from .config import (
    build_classifier_endpoint,
    build_vercel_gpt_oss_endpoint,
    build_worker_endpoints,
)
from .models import LLMEndpoint, LLMGeneration
from .usage import extract_llm_usage

# Note: `insert_llm_call_log` is imported lazily inside `_complete` to avoid a
# circular import: analytics.writers.core pulls in llm.usage, and router.py
# pulls in llm.usage directly. Eagerly importing writers.core here would deadlock.

LOGGER = logging.getLogger(__name__)


# Per-request ContextVars set by the orchestrator entry point.
# The router reads these at LLM-call time so call sites don't have to
# thread run_key/operation through N layers.
_run_key_ctx: ContextVar[str | None] = ContextVar("kindly_run_key", default=None)
_operation_ctx: ContextVar[str] = ContextVar("kindly_operation", default="unknown")


def bind_run_context(run_key: str | None, operation: str) -> Any:
    """Bind the current asyncio task's LLM-call context.

    Returns a token bundle (run_key_token, operation_token); call
    ``reset_run_context(token)`` when the request finishes.
    """
    return _run_key_ctx.set(run_key), _operation_ctx.set(operation)


def reset_run_context(token: tuple[Any, Any]) -> None:
    """Reset the ContextVars set by ``bind_run_context``."""
    rk_token, op_token = token
    _run_key_ctx.reset(rk_token)
    _operation_ctx.reset(op_token)


# Pricing table — USD per 1M tokens (input, output).
# Source: provider public docs and Litellm snapshot 2026-07-21.
# Only active (provider, model) pairs used by the router ladder are listed.
# Unknown pairs return None (not 0.0) so callers can flag `cost_estimated=False`.
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # Cerebras (wafer-scale; only gpt-oss-120b is production per inference-docs.cerebras.ai)
    "cerebras:gpt-oss-120b": (0.35, 0.75),
    # Groq (gpt-oss models per Groq pricing page, June 2026)
    "groq:gpt-oss-120b": (0.15, 0.60),
    "groq:gpt-oss-20b": (0.075, 0.30),
    # Vercel AI Gateway (pass-through; uses Groq rates as best-known estimate)
    "vercel:gpt-oss-20b": (0.10, 0.40),
    # Gemini (Google AI Studio paid tier, per ai.google.dev/gemini-api/docs/pricing)
    "gemini:gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini:gemini-2.5-flash": (0.30, 2.50),
    "gemini:gemini-2.5-flash-lite": (0.10, 0.40),
    # OpenRouter (xAI Grok)
    "openrouter:x-ai/grok-4.3": (3.00, 15.00),
}


def _estimate_cost_usd(
    provider: str, model: str, prompt_tokens: int, completion_tokens: int
) -> float | None:
    """Look up cost for a (provider, model) pair. Returns None for unknown pairs.

    Cost is per-million-token rates from `_MODEL_PRICING`; callers store None
    for unknown pairs (clean audit trail vs. writing 0.0 for unestimated rows).
    """
    model_normalized = _normalize_model_name(model)
    provider_lower = provider.lower()
    model_lower = model_normalized.lower()
    for key in (f"{provider_lower}:{model_lower}", model_lower):
        if key in _MODEL_PRICING:
            in_price, out_price = _MODEL_PRICING[key]
            return (prompt_tokens * in_price + completion_tokens * out_price) / 1_000_000.0
    return None


def _normalize_model_name(model: str) -> str:
    """Normalize model name for pricing lookup.

    Handles:
    - "openai/gpt-oss-120b" -> "gpt-oss-120b"
    - "openai/gpt-oss-120b:nscale" -> "gpt-oss-120b" (provider inferred from caller)
    - "gemini-2.5-flash" -> "gemini-2.5-flash"
    """
    # Strip "openai/" prefix if present
    if model.startswith("openai/"):
        model = model[len("openai/") :]
    # Strip any ":provider" suffix (e.g., "gpt-oss-120b:nscale")
    if ":" in model:
        model = model.split(":")[0]
    return model


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
        run_key: str | None = None,
        operation: str = "unknown",
    ) -> LLMGeneration:
        # Lazy import to break the circular dependency with analytics.writers.core
        from ..analytics.writers.core import insert_llm_call_log as _insert_llm_call_log

        errors: list[Exception] = []
        start_time = time.perf_counter()
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
                    usage = extract_llm_usage(response)
                    generation = LLMGeneration(
                        endpoint=endpoint,
                        content=content,
                        usage=usage,
                        annotations=annotations,
                        provider_specific_fields=provider_specific_fields,
                    )
                    # Log LLM call
                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                    cost_usd = _estimate_cost_usd(
                        endpoint.name,
                        endpoint.model,
                        usage.input_tokens if usage else 0,
                        usage.output_tokens if usage else 0,
                    )
                    try:
                        _insert_llm_call_log(
                            run_key=run_key or _run_key_ctx.get(),
                            call_purpose=operation
                            if operation != "unknown"
                            else _operation_ctx.get(),
                            provider=endpoint.name,
                            model=endpoint.model,
                            input_tokens=usage.input_tokens if usage else 0,
                            output_tokens=usage.output_tokens if usage else 0,
                            tokens_used=usage.total_tokens if usage else 0,
                            cost_usd=cost_usd,
                            duration_ms=elapsed_ms,
                            status="success",
                            error_type=None,
                            payload_json=None,
                        )
                    except Exception as log_exc:
                        # Never let logging break the main flow

                        LOGGER.warning("Failed to log LLM call: %s", log_exc)
                    return generation
                raise RuntimeError(f"{endpoint.name} returned empty content")
            except Exception as exc:  # sequential provider ladder, no hidden fallback
                errors.append(exc)
        # All endpoints failed — log failure
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        try:
            _insert_llm_call_log(
                run_key=run_key,
                call_purpose=operation,
                provider=errors[0].__class__.__name__ if errors else "unknown",
                model=self.endpoints[0].model if self.endpoints else "unknown",
                input_tokens=0,
                output_tokens=0,
                tokens_used=0,
                cost_usd=0.0,
                duration_ms=elapsed_ms,
                status="error",
                error_type=type(errors[0]).__name__ if errors else "UnknownError",
                payload_json=None,
            )
        except Exception as log_exc:
            LOGGER.warning("Failed to log LLM call error: %s", log_exc)
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
        run_key: str | None = None,
        operation: str = "unknown",
    ) -> LLMGeneration:
        return await self._complete(
            messages=messages,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            reasoning_effort=reasoning_effort,
            langfuse=langfuse,
            run_key=run_key,
            operation=operation,
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
        run_key: str | None = None,
        operation: str = "unknown",
    ) -> LLMGeneration:
        return await self._complete(
            messages=messages,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            response_format=response_model or {"type": "json_object"},
            reasoning_effort=reasoning_effort,
            langfuse=langfuse,
            run_key=run_key,
            operation=operation,
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
        run_key: str | None = None,
        operation: str = "unknown",
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
            run_key=run_key,
            operation=operation,
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
