"""RankLLM bridge routing listwise LLM reranking through the unified inference subsystem."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ...models import WebSearchResult
from ...settings import settings
from ...rerank.llm_rerank import (
    LLMRerankOutcome,
    _build_request,
    _get_gemini_coordinator,
    _get_openrouter_coordinator,
    _run_coordinator,
    _CoordinatorGuardTimeout,
)
from ..chain import get_chain
from ..types import ModelSpec
from ..engine import ChainExhaustedError, execute_with_fallback

logger = logging.getLogger(__name__)


async def rerank_with_rankllm_bridge(
    query: str,
    candidates: list[WebSearchResult],
    *,
    request_id: str | None = None,
) -> LLMRerankOutcome:
    """Rerank candidates using RankLLM routed through the unified inference engine."""
    if not candidates:
        return LLMRerankOutcome("bypass", None, [])

    request = _build_request(query, candidates, request_id or "rerank-request")
    chain = get_chain("rankllm")

    async def _handle_rankllm_spec(spec: ModelSpec) -> tuple[Any, int | None, int | None]:
        if spec.provider == "google":
            coordinator = _get_gemini_coordinator(spec.model_id)
        elif spec.provider == "openrouter":
            coordinator = _get_openrouter_coordinator()
        else:
            coordinator = None

        if coordinator is None:
            raise ValueError(f"No coordinator available for RankLLM provider {spec.provider}")

        return await _run_coordinator(coordinator, request, len(candidates))

    def _is_retryable(exc: Exception) -> bool:
        # Stop fallback immediately if coordinator guard outer timeout elapsed
        return not isinstance(exc, _CoordinatorGuardTimeout)

    try:
        exec_res = await asyncio.wait_for(
            execute_with_fallback(
                chain,
                operation="rankllm_listwise",
                handler=_handle_rankllm_spec,
                is_retryable=_is_retryable,
            ),
            timeout=settings.rankllm_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        logger.warning(
            "RankLLM fallback chain exceeded its total timeout of %.1fs; failing open",
            settings.rankllm_timeout_seconds,
        )
        return LLMRerankOutcome("chain_timeout", None, [], error=exc)
    except ChainExhaustedError as exc:
        last_error = exc.errors[-1][1] if exc.errors else exc
        return LLMRerankOutcome("chain_failed", None, [], error=last_error)

    ranked, input_tokens, output_tokens = exec_res.payload
    return LLMRerankOutcome(
        endpoint_name=exec_res.spec.provider,
        model=exec_res.spec.model_id,
        ranked=ranked,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
