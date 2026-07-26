"""Worker facade for structured and unstructured LLM task routing."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from .types import LLMGeneration, LLMUsage
from ..telemetry.phoenix_tracing import LLMTraceContext
from .chain import get_chain
from .engine import (
    bind_run_context,
    current_operation,
    current_run_key,
    execute_with_fallback,
    reset_run_context,
)


@dataclass(frozen=True, slots=True)
class StructuredLLMRequest:
    task: str
    messages: list[dict[str, str]]
    temperature: float = 0.0
    timeout_seconds: float | None = None
    response_model: type[BaseModel] | None = None
    reasoning_effort: str | None = None
    langfuse: LLMTraceContext | None = None
    run_key: str | None = None
    operation: str = "unknown"


@dataclass(frozen=True, slots=True)
class StructuredLLMResponse:
    endpoint_name: str
    model_name: str
    content: str
    usage: LLMUsage | None = None


@dataclass(frozen=True, slots=True)
class LLMWorker:
    """Worker task router executing through the unified inference engine."""

    async def complete_structured(
        self,
        request: StructuredLLMRequest,
    ) -> StructuredLLMResponse:
        chain_name = "classifier_llm" if request.task == "query_understand" else "worker_llm"
        chain = get_chain(chain_name)
        operation = request.operation if request.operation != "unknown" else current_operation()
        if operation == "unknown":
            operation = request.task
        run_key = request.run_key if request.run_key is not None else current_run_key()
        context_token = bind_run_context(run_key, operation)

        try:
            exec_res = await execute_with_fallback(
                chain,
                operation=operation,
                messages=request.messages,
                temperature=request.temperature,
                timeout_seconds=request.timeout_seconds,
                response_format=request.response_model or {"type": "json_object"},
                reasoning_effort=request.reasoning_effort,
                langfuse=request.langfuse,
            )
        finally:
            reset_run_context(context_token)
        gen: LLMGeneration = exec_res.payload
        return StructuredLLMResponse(
            endpoint_name=gen.spec.provider,
            model_name=gen.spec.model_id,
            content=gen.content,
            usage=gen.usage,
        )

    async def complete_json(
        self,
        *,
        task: str,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        timeout_seconds: float | None = None,
        reasoning_effort: str | None = None,
        langfuse: LLMTraceContext | None = None,
        run_key: str | None = None,
        operation: str = "unknown",
    ) -> StructuredLLMResponse:
        return await self.complete_structured(
            StructuredLLMRequest(
                task=task,
                messages=messages,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
                reasoning_effort=reasoning_effort,
                langfuse=langfuse,
                run_key=run_key,
                operation=operation,
            )
        )


def build_llm_worker() -> LLMWorker:
    return LLMWorker()
