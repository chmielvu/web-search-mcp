"""Bounded RankLLM listwise reranking with application-owned fallback."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..prompts.rerank_llm import load_rerank_system_message
from ..search.types import ScoredHit
from ..settings import settings
from .models import FINAL_RESULT_LIMIT, RankedStageOutcome, RerankResult
from .utils import _apply_ranked_stage, _failed_stage

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
    attempted_passes: int = 0
    valid_passes: int = 0
    failed_passes: int = 0


class _CoordinatorGuardTimeout(TimeoutError):
    """The provider call outlived its transport timeout guard."""


def _load_rank_llm_openai() -> tuple[Any, Any, Any, Any]:
    """Lazy-load rank_llm SafeOpenai path to avoid pulling in vllm or litellm."""
    import sys

    import huggingface_hub

    if not hasattr(huggingface_hub, "is_offline_mode"):
        huggingface_hub.is_offline_mode = lambda: False

    # NOTE: load-bearing stub. rank_llm's listwise package eagerly imports
    # vllm (rank_llm/rerank/listwise/__init__.py -> rank_listwise_os_llm.py),
    # which is not installed. Removing this breaks the SafeOpenai/SafeGenai
    # import path; a proper fix requires an upstream lazy-import change.
    from unittest.mock import MagicMock

    for mod in (
        "vllm",
        "rank_llm.rerank.listwise.rank_listwise_os_llm",
        "rank_llm.rerank.reranker",
    ):
        if mod not in sys.modules:
            sys.modules[mod] = MagicMock()

    from rank_llm.data import Candidate, Query, Request
    from rank_llm.rerank.listwise.rank_gpt import SafeOpenai

    return Candidate, Query, Request, SafeOpenai


def _load_rank_llm_genai() -> Any:
    """Lazy-load SafeGenai from rank_llm without touching the litellm path."""
    import sys

    import huggingface_hub

    if not hasattr(huggingface_hub, "is_offline_mode"):
        huggingface_hub.is_offline_mode = lambda: False
    from unittest.mock import MagicMock

    for mod in (
        "vllm",
        "rank_llm.rerank.listwise.rank_listwise_os_llm",
        "rank_llm.rerank.reranker",
    ):
        if mod not in sys.modules:
            sys.modules[mod] = MagicMock()

    from rank_llm.rerank.listwise.rank_gemini import SafeGenai

    return SafeGenai


def _make_bounded_openai_class() -> type:
    _, _, _, SafeOpenai = _load_rank_llm_openai()

    class BoundedSafeOpenai(SafeOpenai):  # type: ignore[misc]
        """Direct openai.responses reranker — no litellm dependency."""

    return BoundedSafeOpenai


_bounded_openai_class: type | None = None
_bounded_openai_class_LOCK = threading.RLock()


def _get_bounded_openai_class() -> type:
    global _bounded_openai_class
    if _bounded_openai_class is None:
        with _bounded_openai_class_LOCK:
            if _bounded_openai_class is None:
                _bounded_openai_class = _make_bounded_openai_class()
    return _bounded_openai_class


def _make_bounded_genai_class() -> type:
    SafeGenai = _load_rank_llm_genai()

    class BoundedSafeGenai(SafeGenai):  # type: ignore[misc]
        """Direct google-genai reranker — no litellm dependency."""

    return BoundedSafeGenai


_bounded_genai_class_LOCK = threading.RLock()
_bounded_genai_class: type | None = None


def _get_bounded_genai_class() -> type:
    global _bounded_genai_class
    if _bounded_genai_class is None:
        with _bounded_genai_class_LOCK:
            if _bounded_genai_class is None:
                _bounded_genai_class = _make_bounded_genai_class()
    return _bounded_genai_class


_openrouter_coordinator: type | None = None
_gemini_coordinators: dict[str, Any] = {}


def _build_openai_coordinator(
    *, model: str, context_size: int, api_key: str, base_url: str | None
) -> Any:
    BoundedSafeOpenai = _get_bounded_openai_class()
    return BoundedSafeOpenai(
        model=model,
        context_size=context_size,
        prompt_template_path=str(_TEMPLATE_PATH),
        window_size=settings.rankllm_window_size,
        stride=settings.rankllm_stride,
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
        window_size=settings.rankllm_window_size,
        stride=settings.rankllm_stride,
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
    candidates: list[ScoredHit],
    request_id: str,
) -> Any:
    Candidate, Query, Request, _ = _load_rank_llm_openai()
    rank_candidates = [
        Candidate(
            docid=str(index),
            doc={
                "title": candidate.hit.title,
                "content": (
                    f"Title: {candidate.hit.title}\n"
                    f"Snippet: {candidate.hit.snippet}\n"
                    f"URL: {candidate.hit.url}\n"
                    f"Domain: {candidate.hit.domain or 'unknown'}\n"
                    f"Providers: {', '.join(candidate.providers or []) or 'unknown'}\n"
                    f"ProviderCount: {len(candidate.providers) if candidate.providers else 1}"
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
    return [
        RerankResult(index=int(candidate.docid), relevance_score=1.0 / (60 + position))
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
) -> tuple[
    list[RerankResult],
    int | None,
    int | None,
    int,
    int,
    int,
    Exception | None,
]:
    """Run shuffled listwise passes and aggregate successful passes via Borda.

    Permutation self-consistency (Found in the Middle, ACL 2024): aggregating
    positions across independently shuffled passes reduces positional bias.
    Borda count (sum of positions, ascending) is a valid PSC aggregation
    alongside Kemeny-Young and RRF. Passes run concurrently; the
    coordinator's shared LLM-slot semaphore caps actual in-flight calls.
    """
    num_passes = max(1, int(settings.rankllm_num_passes))

    async def _run_pass() -> tuple[list[RerankResult], int | None, int | None]:
        async def _cancel_and_drain() -> None:
            if not task.done():
                task.cancel()
            # Cancellation and provider failures are intentionally consumed
            # after the coordinator task has been awaited, preventing unhandled
            # RankLLM child-task warnings on the MCP event loop.
            with contextlib.suppress(BaseException):
                await task

        task = asyncio.create_task(
            coordinator.rerank_batch_async(
                [copy.deepcopy(request)],
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

    pass_results = await asyncio.gather(
        *(_run_pass() for _ in range(num_passes)),
        return_exceptions=True,
    )
    ranked_passes: list[list[RerankResult]] = []
    input_tokens = 0
    output_tokens = 0
    passes_failed = 0
    first_error: Exception | None = None
    for item in pass_results:
        if isinstance(item, BaseException):
            passes_failed += 1
            if not isinstance(item, Exception):
                raise item
            first_error = first_error or item
            continue
        ranked, in_tokens, out_tokens = item
        ranked_passes.append(ranked)
        input_tokens += in_tokens or 0
        output_tokens += out_tokens or 0
    passes_succeeded = len(ranked_passes)
    if not ranked_passes:
        if first_error is not None:
            raise first_error
        raise RuntimeError("RankLLM returned no successful passes")
    if num_passes == 1:
        return (
            ranked_passes[0],
            input_tokens or None,
            output_tokens or None,
            num_passes,
            passes_succeeded,
            passes_failed,
            first_error,
        )

    # Borda count: sum each candidate's position across successful passes.
    position_sums: dict[int, float] = {}
    for ranked in ranked_passes:
        for position, item in enumerate(ranked):
            position_sums[item.index] = position_sums.get(item.index, 0.0) + position
    order = sorted(position_sums, key=lambda index: (position_sums[index], index))
    ranked = [
        RerankResult(index=index, relevance_score=1.0 / (60 + position))
        for position, index in enumerate(order)
    ]
    return (
        ranked,
        input_tokens or None,
        output_tokens or None,
        num_passes,
        passes_succeeded,
        passes_failed,
        first_error,
    )


async def rerank_with_llm(
    query: str,
    candidates: list[ScoredHit],
    *,
    request_id: str | None = None,
) -> LLMRerankOutcome:
    """Rerank one complete window via OpenRouter, then Gemini, or fail open."""
    from ..inference.bridges.rankllm import rerank_with_rankllm_bridge

    return await rerank_with_rankllm_bridge(query, candidates, request_id=request_id)


async def run_llm_stage(
    *,
    query: str,
    candidates: list[ScoredHit],
    request_id: str | None,
    query_type_hint: str | None,
    run_key: str | None,
    main_span: Any,
    logger: logging.Logger,
) -> RankedStageOutcome:
    del query_type_hint
    stage_start = time.monotonic()
    try:
        outcome = await rerank_with_llm(query, candidates, request_id=request_id)
    except Exception as exc:
        outcome = None
        error = exc
    else:
        error = outcome.error
    duration_seconds = time.monotonic() - stage_start

    if outcome is None or not outcome.ranked:
        return _failed_stage(
            stage_name="rankllm",
            provider=outcome.endpoint_name if outcome else "chain_failed",
            model=outcome.model if outcome else None,
            candidates=candidates,
            output_limit=FINAL_RESULT_LIMIT,
            duration_seconds=duration_seconds,
            error=error,
            input_tokens=outcome.input_tokens if outcome else None,
            output_tokens=outcome.output_tokens if outcome else None,
            attempted_passes=outcome.attempted_passes if outcome else 0,
            valid_passes=outcome.valid_passes if outcome else 0,
            failed_passes=outcome.failed_passes if outcome else 0,
        )

    try:
        return await _apply_ranked_stage(
            stage_name="rankllm",
            provider=outcome.endpoint_name,
            model=outcome.model,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            input_candidates=candidates,
            ranked_results=outcome.ranked,
            duration_seconds=duration_seconds,
            run_key=run_key,
            main_span=main_span,
            logger=logger,
            output_limit=FINAL_RESULT_LIMIT,
            error=error,
            attempted_passes=outcome.attempted_passes,
            valid_passes=outcome.valid_passes,
            failed_passes=outcome.failed_passes,
        )
    except (TypeError, ValueError) as exc:
        return _failed_stage(
            stage_name="rankllm",
            provider=outcome.endpoint_name,
            model=outcome.model,
            candidates=candidates,
            output_limit=FINAL_RESULT_LIMIT,
            duration_seconds=duration_seconds,
            error=exc,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            attempted_passes=outcome.attempted_passes,
            valid_passes=outcome.valid_passes,
            failed_passes=outcome.failed_passes,
        )
