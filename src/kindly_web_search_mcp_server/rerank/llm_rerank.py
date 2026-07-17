"""Bounded RankLLM listwise reranking with application-owned fallback."""

from __future__ import annotations
import asyncio
from dataclasses import dataclass
from contextlib import redirect_stdout
import logging
import io
import os
from pathlib import Path
from typing import Any


from ..models import WebSearchResult
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
_gemini_coordinator: type | None = None


def _route_model(provider: str, model: str) -> str:
    prefix = f"{provider}/"
    return model if model.startswith(prefix) else f"{prefix}{model}"


def _build_openai_coordinator(
    *, model: str, context_size: int, api_key: str, base_url: str | None
) -> Any:
    BoundedSafeOpenai = _get_bounded_openai_class()
    captured_stdout = io.StringIO()
    with redirect_stdout(captured_stdout):
        coordinator = BoundedSafeOpenai(
            model=model,
            context_size=context_size,
            prompt_template_path=str(_TEMPLATE_PATH),
            window_size=RANKLLM_INPUT_LIMIT,
            stride=RANKLLM_INPUT_LIMIT,
            max_passage_words=settings.rankllm_max_passage_words,
            keys=api_key,
            base_url=base_url,
        )
    if output := captured_stdout.getvalue().strip():
        logger.debug("Suppressed SafeOpenai constructor stdout: %s", output)
    return coordinator


def _build_genai_coordinator(*, model: str, context_size: int, api_key: str) -> Any:
    BoundedSafeGenai = _get_bounded_genai_class()
    captured_stdout = io.StringIO()
    with redirect_stdout(captured_stdout):
        coordinator = BoundedSafeGenai(
            model=model,
            context_size=context_size,
            prompt_template_path=str(_TEMPLATE_PATH),
            window_size=RANKLLM_INPUT_LIMIT,
            stride=RANKLLM_INPUT_LIMIT,
            max_passage_words=settings.rankllm_max_passage_words,
            keys=api_key,
            temperature=settings.rankllm_temperature,
        )
    if output := captured_stdout.getvalue().strip():
        logger.debug("Suppressed SafeGenai constructor stdout: %s", output)
    return coordinator


def _get_openrouter_coordinator() -> Any | None:
    global _openrouter_coordinator
    if _openrouter_coordinator is not None:
        return _openrouter_coordinator
    api_key = (settings.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")).strip()
    if not api_key:
        return None
    _openrouter_coordinator = _build_openai_coordinator(
        model=settings.rankllm_openrouter_model,
        context_size=131_072,
        api_key=api_key,
        base_url=settings.openrouter_chat_base_url,
    )
    return _openrouter_coordinator


def _get_gemini_coordinator() -> Any | None:
    global _gemini_coordinator
    if _gemini_coordinator is not None:
        return _gemini_coordinator
    api_key = (settings.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")).strip()
    if not api_key:
        return None
    _gemini_coordinator = _build_genai_coordinator(
        model=settings.rankllm_gemini_model,
        context_size=1_048_576,
        api_key=api_key,
    )
    return _gemini_coordinator


def _candidate_content(result: WebSearchResult) -> str:
    snippet = (result.snippet or "")[:1_500]
    providers = ", ".join(result.providers or []) or "unknown"
    return (
        f"Snippet: {snippet}\n"
        f"URL: {result.link}\n"
        f"Domain: {result.domain or 'unknown'}\n"
        f"Providers: {providers}\n"
        f"ProviderCount: {result.provider_count or 1}"
    )


def _build_request(
    query: str,
    candidates: list[WebSearchResult],
    request_id: str,
) -> Any:
    Candidate, Query, Request, _ = _load_rank_llm_openai()
    rankllm_candidates = [
        Candidate(
            docid=index,
            score=float(result.score or 0.0),
            doc={"title": result.title, "content": _candidate_content(result)},
        )
        for index, result in enumerate(candidates)
    ]
    return Request(
        query=Query(text=query, qid=request_id),
        candidates=rankllm_candidates,
    )


def _ranked_permutation(result: Any, candidate_count: int) -> list[RerankResult]:
    returned_ids = [candidate.docid for candidate in result.candidates]
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
    task = asyncio.create_task(
        coordinator.rerank_batch_async(
            [request],
            rank_start=0,
            rank_end=candidate_count,
            shuffle_candidates=False,
            logging=False,
            populate_invocations_history=True,
        )
    )
    outer_timeout = settings.rankllm_timeout_seconds + 5.0
    try:
        results = await asyncio.wait_for(asyncio.shield(task), timeout=outer_timeout)
    except TimeoutError as exc:
        if task.done():
            raise
        task.cancel()
        raise _CoordinatorGuardTimeout(
            "RankLLM provider call did not terminate within the outer guard"
        ) from exc
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
    if not candidates:
        return LLMRerankOutcome("bypass", None, [])

    request = _build_request(query, candidates, request_id or "rerank-request")
    errors: list[Exception] = []
    routes = (
        ("gemini", settings.rankllm_gemini_model, _get_gemini_coordinator),
        ("openrouter", settings.rankllm_openrouter_model, _get_openrouter_coordinator),
    )
    for endpoint_name, model, factory in routes:
        coordinator = factory()
        if coordinator is None:
            continue
        try:
            ranked, input_tokens, output_tokens = await _run_coordinator(
                coordinator,
                request,
                len(candidates),
            )
            return LLMRerankOutcome(
                endpoint_name=endpoint_name,
                model=model,
                ranked=ranked,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        except Exception as exc:
            errors.append(exc)
            logger.warning(
                "%s RankLLM failed: %s: %s",
                endpoint_name,
                type(exc).__name__,
                exc,
            )
            if isinstance(exc, _CoordinatorGuardTimeout):
                break

    error = errors[-1] if errors else ValueError("No RankLLM provider credential is configured")
    return LLMRerankOutcome("chain_failed", None, [], error=error)
