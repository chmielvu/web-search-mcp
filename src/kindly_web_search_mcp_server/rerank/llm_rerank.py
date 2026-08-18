"""Bounded RankLLM listwise reranking with application-owned fallback."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import WebSearchResult
from ..prompts.rerank_llm import load_rerank_system_message
from ..settings import settings
from .limits import RANKLLM_INPUT_LIMIT
from .models import RerankResult

logger = logging.getLogger(__name__)
_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "prompts" / "rerank_llm.yaml"


@dataclass(frozen=True, slots=True)
class LLMRerankOutcome:
    endpoint_name: str
    model: str | None
    ranked: list[RerankResult]
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: Exception | None = None


class _CoordinatorGuardTimeout(TimeoutError):
    """The provider call outlived its transport timeout guard."""


def _load_rank_llm_openai() -> tuple[Any, Any, Any, Any]:
    """Lazy-load rank_llm SafeOpenai path to avoid pulling in vllm or litellm."""
    from rank_llm.data import Candidate, Query, Request  # noqa: PLC0415
    from rank_llm.rerank.listwise.rank_gpt import SafeOpenai  # noqa: PLC0415

    return Candidate, Query, Request, SafeOpenai


def _load_rank_llm_genai() -> Any:
    """Lazy-load SafeGenai from rank_llm without touching the litellm path."""
    from rank_llm.rerank.listwise.rank_gemini import SafeGenai  # noqa: PLC0415

    return SafeGenai


def _make_bounded_openai_class() -> type:
    _, _, _, SafeOpenai = _load_rank_llm_openai()

    class BoundedSafeOpenai(SafeOpenai):  # type: ignore[misc]
        """Direct openai.responses reranker — no litellm dependency."""

    return BoundedSafeOpenai


_bounded_openai_class: type | None = None


def _get_bounded_openai_class() -> type:
    global _bounded_openai_class
    if _bounded_openai_class is None:
        _bounded_openai_class = _make_bounded_openai_class()
    return _bounded_openai_class


def _make_bounded_genai_class() -> type:
    SafeGenai = _load_rank_llm_genai()

    class BoundedSafeGenai(SafeGenai):  # type: ignore[misc]
        """Direct google-genai reranker — no litellm dependency."""

    return BoundedSafeGenai


_bounded_genai_class: type | None = None


def _get_bounded_genai_class() -> type:
    global _bounded_genai_class
    if _bounded_genai_class is None:
        _bounded_genai_class = _make_bounded_genai_class()
    return _bounded_genai_class


_openrouter_coordinator: type | None = None
_gemini_coordinators: dict[str, Any] = {}


def _route_model(provider: str, model: str) -> str:
    prefix = f"{provider}/"
    return model if model.startswith(prefix) else f"{prefix}{model}"


def _build_openai_coordinator(
    *, model: str, context_size: int, api_key: str, base_url: str | None
) -> Any:
    BoundedSafeOpenai = _get_bounded_openai_class()
    return BoundedSafeOpenai(
        model=model,
        context_size=context_size,
        prompt_template_path=str(_TEMPLATE_PATH),
        window_size=RANKLLM_INPUT_LIMIT,
        stride=RANKLLM_INPUT_LIMIT,
        max_passage_words=settings.rankllm_max_passage_words,
        keys=api_key,
        base_url=base_url,
    )


def _build_genai_coordinator(*, model: str, api_key: str) -> Any:
    BoundedSafeGenai = _get_bounded_genai_class()
    return BoundedSafeGenai(
        model=model,
        context_size=1_048_576,
        prompt_template_path=str(_TEMPLATE_PATH),
        window_size=RANKLLM_INPUT_LIMIT,
        stride=RANKLLM_INPUT_LIMIT,
        max_passage_words=settings.rankllm_max_passage_words,
        keys=api_key,
        temperature=settings.rankllm_temperature,
        system_instruction=load_rerank_system_message(),
    )


def _get_openrouter_coordinator() -> Any:
    global _openrouter_coordinator
    if _openrouter_coordinator is not None:
        return _openrouter_coordinator
    api_key = settings.openrouter_api_key
    if not api_key:
        return None
    model = settings.rankllm_openrouter_model
    _openrouter_coordinator = _build_openai_coordinator(
        model=model,
        context_size=131_072,
        api_key=api_key,
        base_url=settings.openrouter_chat_base_url,
    )
    return _openrouter_coordinator


def _get_gemini_coordinator(model: str | None = None) -> Any:
    api_key = settings.gemini_api_key
    if not api_key:
        return None
    model_id = model or settings.rankllm_gemini_model
    if model_id not in _gemini_coordinators:
        _gemini_coordinators[model_id] = _build_genai_coordinator(
            model=model_id,
            api_key=api_key,
        )
    return _gemini_coordinators[model_id]


def _build_request(
    query: str,
    candidates: list[WebSearchResult],
    request_id: str,
) -> Any:
    Candidate, Query, Request, _ = _load_rank_llm_openai()
    rank_candidates = [
        Candidate(
            docid=str(index),
            doc={
                "title": candidate.title,
                "content": (
                    f"Title: {candidate.title}\n"
                    f"Snippet: {candidate.snippet}\n"
                    f"URL: {candidate.link}\n"
                    f"Domain: {candidate.domain or 'unknown'}\n"
                    f"Providers: {', '.join(candidate.providers or []) or 'unknown'}\n"
                    f"ProviderCount: {candidate.provider_count or 1}"
                ),
            },
            score=0.0,
        )
        for index, candidate in enumerate(candidates)
    ]
    return Request(
        query=Query(text=query, qid=request_id),
        candidates=rank_candidates,
    )


def _ranked_permutation(result: Any, candidate_count: int) -> list[RerankResult]:
    try:
        returned_ids = [int(candidate.docid) for candidate in result.candidates]
    except (TypeError, ValueError) as exc:
        raise ValueError("RankLLM result contains a non-integer candidate id") from exc
    expected_ids = set(range(candidate_count))
    if len(returned_ids) != candidate_count or set(returned_ids) != expected_ids:
        raise ValueError("RankLLM result is not a complete candidate permutation")
    denominator = max(candidate_count - 1, 1)
    return [
        RerankResult(index=int(candidate.docid), score=1.0 - position / denominator)
        for position, candidate in enumerate(result.candidates)
    ]


def _token_counts(result: Any) -> tuple[int | None, int | None]:
    invocations = result.invocations_history or []
    input_tokens = sum(invocation.input_token_count or 0 for invocation in invocations)
    output_tokens = sum(invocation.output_token_count or 0 for invocation in invocations)
    return input_tokens or None, output_tokens or None


async def _run_coordinator(
    coordinator: Any,
    request: Any,
    candidate_count: int,
) -> tuple[list[RerankResult], int | None, int | None]:
    async def _cancel_and_drain() -> None:
        if not task.done():
            task.cancel()
        try:
            await task
        except BaseException:
            # Cancellation and provider failures are intentionally consumed
            # after the coordinator task has been awaited, preventing
            # unhandled RankLLM child-task warnings on the MCP event loop.
            pass

    task = asyncio.create_task(
        coordinator.rerank_batch_async(
            [request],
            rank_start=0,
            rank_end=candidate_count,
            shuffle_candidates=True,
            logging=False,
            populate_invocations_history=True,
        )
    )
    outer_timeout = settings.rankllm_timeout_seconds + 5.0
    try:
        results = await asyncio.wait_for(asyncio.shield(task), timeout=outer_timeout)
    except TimeoutError as exc:
        await _cancel_and_drain()
        raise _CoordinatorGuardTimeout(
            "RankLLM provider call did not terminate within the outer guard"
        ) from exc
    except asyncio.CancelledError:
        await _cancel_and_drain()
        raise
    if len(results) != 1:
        raise ValueError("RankLLM returned an unexpected result batch")
    ranked = _ranked_permutation(results[0], candidate_count)
    input_tokens, output_tokens = _token_counts(results[0])
    return ranked, input_tokens, output_tokens


async def rerank_with_llm(
    query: str,
    candidates: list[WebSearchResult],
    *,
    request_id: str | None = None,
) -> LLMRerankOutcome:
    """Rerank one complete window via OpenRouter, then Gemini, or fail open."""
    from ..inference.bridges.rankllm import rerank_with_rankllm_bridge

    return await rerank_with_rankllm_bridge(query, candidates, request_id=request_id)
