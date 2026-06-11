"""Facade for structured and unstructured LLM calls."""

from __future__ import annotations

from dataclasses import dataclass

from .router import build_classifier_router, build_worker_router
from .structured import StructuredLLMRequest, StructuredLLMResponse


@dataclass(frozen=True, slots=True)
class LLMWorker:
    """Small task router for the 0.2 backend."""

    async def complete_structured(
        self, request: StructuredLLMRequest
    ) -> StructuredLLMResponse:
        router = (
            build_classifier_router()
            if request.task == "query_understand"
            else build_worker_router()
        )
        generation = await router.complete_json(
            messages=request.messages,
            temperature=request.temperature,
            timeout_seconds=request.timeout_seconds,
            response_model=request.response_model,
        )
        return StructuredLLMResponse(
            endpoint_name=generation.endpoint.name,
            model_name=generation.endpoint.model,
            content=generation.content,
        )

    async def complete_json(
        self,
        *,
        task: str,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        timeout_seconds: float | None = None,
    ) -> StructuredLLMResponse:
        return await self.complete_structured(
            StructuredLLMRequest(
                task=task,
                messages=messages,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
            )
        )

    async def complete_text(self, *, task: str, prompt: str) -> str:
        result = await self.complete_json(
            task=task,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        return result.content


def build_llm_worker() -> LLMWorker:
    return LLMWorker()
