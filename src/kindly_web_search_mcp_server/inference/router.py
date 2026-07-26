"""Ordered OpenAI-compatible routing for classification and rewrite tasks."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from .chain import ChainSpec, get_chain
from .engine import ChainExhaustedError, current_operation, current_run_key, execute_with_fallback
from .types import LLMGeneration
from ..telemetry.phoenix_tracing import LLMTraceContext

LOGGER = logging.getLogger(__name__)


_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "cerebras:gpt-oss-120b": (0.35, 0.75),
    "groq:gpt-oss-120b": (0.15, 0.60),
    "groq:gpt-oss-20b": (0.075, 0.30),
    "vercel:gpt-oss-20b": (0.10, 0.40),
    "gemini:gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini:gemini-2.5-flash": (0.30, 2.50),
    "gemini:gemini-2.5-flash-lite": (0.10, 0.40),
    "openrouter:x-ai/grok-4.3": (3.00, 15.00),
}


def _estimate_cost_usd(
    provider: str, model: str, prompt_tokens: int, completion_tokens: int
) -> float | None:
    model_normalized = _normalize_model_name(model)
    provider_lower = provider.lower()
    model_lower = model_normalized.lower()
    for key in (f"{provider_lower}:{model_lower}", model_lower):
        if key in _MODEL_PRICING:
            in_price, out_price = _MODEL_PRICING[key]
            return (prompt_tokens * in_price + completion_tokens * out_price) / 1_000_000.0
    return None


def _normalize_model_name(model: str) -> str:
    if model.startswith("openai/"):
        model = model[len("openai/") :]
    if ":" in model:
        model = model.split(":")[0]
    return model


@dataclass(frozen=True, slots=True)
class LLMRouter:
    """Sequential router across provider endpoints using unified engine."""

    chain: ChainSpec

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
        from ..analytics.writers.core import insert_llm_call_log as _insert_llm_call_log

        if not self.chain.model_spec_ids:
            raise RuntimeError("LLMRouter has no configured chain")

        start_time = time.perf_counter()

        effective_run_key = run_key if run_key is not None else current_run_key()
        effective_operation = operation if operation != "unknown" else current_operation()

        try:
            exec_res = await execute_with_fallback(
                self.chain,
                operation=effective_operation,
                messages=messages,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
                response_format=response_format,
                reasoning_effort=reasoning_effort,
                langfuse=langfuse,
                tools=tools,
                web_search_options=web_search_options,
                provider_fields=provider_fields,
            )
            generation = exec_res.payload
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            cost_usd = _estimate_cost_usd(
                generation.spec.provider,
                generation.spec.model_id,
                generation.input_tokens or 0,
                generation.output_tokens or 0,
            )
            try:
                _insert_llm_call_log(
                    run_key=effective_run_key,
                    call_purpose=effective_operation,
                    provider=generation.spec.provider,
                    model=generation.spec.model_id,
                    input_tokens=generation.input_tokens or 0,
                    output_tokens=generation.output_tokens or 0,
                    tokens_used=(generation.input_tokens or 0) + (generation.output_tokens or 0),
                    cost_usd=cost_usd,
                    duration_ms=elapsed_ms,
                    status="success",
                    error_type=None,
                    payload_json=None,
                )
            except Exception as log_exc:
                LOGGER.warning("Failed to log LLM call: %s", log_exc)
            return generation
        except ChainExhaustedError as exc:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            first_err = exc.errors[0][1] if exc.errors else exc
            first_spec = exc.errors[0][0] if exc.errors else self.chain.primary
            try:
                _insert_llm_call_log(
                    run_key=effective_run_key,
                    call_purpose=effective_operation,
                    provider=first_spec.provider,
                    model=first_spec.model_id,
                    input_tokens=0,
                    output_tokens=0,
                    tokens_used=0,
                    cost_usd=0.0,
                    duration_ms=elapsed_ms,
                    status="error",
                    error_type=type(first_err).__name__,
                    payload_json=None,
                )
            except Exception as log_exc:
                LOGGER.warning("Failed to log LLM call error: %s", log_exc)
            raise RuntimeError(
                "All LLM endpoints failed: "
                + "; ".join(f"{type(e).__name__}: {e}" for _, e in exc.errors)
            ) from exc

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
    return LLMRouter(chain=get_chain("classifier_llm"))


def build_worker_router() -> LLMRouter:
    return LLMRouter(chain=get_chain("worker_llm"))
