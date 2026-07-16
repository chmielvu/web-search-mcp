"""Bounded RankLLM listwise reranking with application-owned fallback."""

from __future__ import annotations

import ast
import asyncio
from dataclasses import dataclass
from contextlib import redirect_stdout
import logging
import io
import os
from pathlib import Path
import re
from typing import Any

import litellm
from rank_llm.data import Candidate, Query, Request
from rank_llm.rerank.listwise.rank_litellm import SafeLiteLLM

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


class BoundedSafeLiteLLM(SafeLiteLLM):
    """Use one bounded LiteLLM call and propagate provider failures."""

    def _call_completion(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        call_kwargs = {**self._call_kwargs(), **kwargs, "num_retries": 0}

        model_attr = getattr(self, "model", "") or ""
        if "openrouter" in model_attr or "openrouter" in call_kwargs.get("model", ""):
            call_kwargs["custom_llm_provider"] = "openrouter"
            call_kwargs["api_base"] = settings.openrouter_chat_base_url
            model_name = call_kwargs.get("model", model_attr)
            if not model_name.startswith("openrouter/"):
                model_name = f"openrouter/{model_name}"
            call_kwargs["model"] = model_name

        async def complete() -> Any:
            return await asyncio.wait_for(
                litellm.acompletion(
                    messages=messages,
                    timeout=settings.rankllm_timeout_seconds,
                    **call_kwargs,
                ),
                timeout=settings.rankllm_timeout_seconds,
            )

        return asyncio.run(complete())


_openrouter_coordinator: BoundedSafeLiteLLM | None = None
_gemini_coordinator: BoundedSafeLiteLLM | None = None


def _route_model(provider: str, model: str) -> str:
    prefix = f"{provider}/"
    return model if model.startswith(prefix) else f"{prefix}{model}"


def _build_coordinator(*, model: str, context_size: int, api_key: str) -> BoundedSafeLiteLLM:
    captured_stdout = io.StringIO()
    with redirect_stdout(captured_stdout):
        coordinator = BoundedSafeLiteLLM(
            model=model,
            context_size=context_size,
            prompt_template_path=str(_TEMPLATE_PATH),
            window_size=RANKLLM_INPUT_LIMIT,
            stride=RANKLLM_INPUT_LIMIT,
            max_passage_words=settings.rankllm_max_passage_words,
            api_key=api_key,
            sampling_kwargs={"temperature": settings.rankllm_temperature},
        )
    if output := captured_stdout.getvalue().strip():
        logger.debug("Suppressed RankLLM constructor stdout: %s", output)
    return coordinator


def _get_openrouter_coordinator() -> BoundedSafeLiteLLM | None:
    global _openrouter_coordinator
    if _openrouter_coordinator is not None:
        return _openrouter_coordinator
    api_key = (settings.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")).strip()
    if not api_key:
        return None
    _openrouter_coordinator = _build_coordinator(
        model=_route_model("openrouter", settings.rankllm_openrouter_model),
        context_size=131_072,
        api_key=api_key,
    )
    return _openrouter_coordinator


def _get_gemini_coordinator() -> BoundedSafeLiteLLM | None:
    global _gemini_coordinator
    if _gemini_coordinator is not None:
        return _gemini_coordinator
    api_key = (settings.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")).strip()
    if not api_key:
        return None
    _gemini_coordinator = _build_coordinator(
        model=_route_model("gemini", settings.rankllm_gemini_model),
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
) -> Request:
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


def _window_size_from_prompt(prompt: Any) -> int:
    if not isinstance(prompt, list):
        return 0
    return sum(
        message.get("role") == "assistant"
        and str(message.get("content", "")).startswith("Received passage [")
        for message in prompt
        if isinstance(message, dict)
    )


def _regex_pattern(value: str | None, *, field_name: str) -> str:
    if not value:
        raise ValueError(f"RankLLM response has no {field_name} contract")
    try:
        pattern = ast.literal_eval(value)
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f"RankLLM {field_name} contract is invalid") from exc
    if not isinstance(pattern, str):
        raise ValueError(f"RankLLM {field_name} contract is not a string")
    return pattern


def _validate_raw_invocations(result: Any) -> None:
    invocations = result.invocations_history or []
    if not invocations:
        raise ValueError("RankLLM returned no invocation history")
    for invocation in invocations:
        response = str(invocation.response).strip()
        validation = _regex_pattern(
            invocation.output_validation_regex,
            field_name="permutation format",
        )
        extraction = _regex_pattern(
            invocation.output_extraction_regex,
            field_name="identifier extraction",
        )
        if re.fullmatch(validation, response) is None:
            raise ValueError("RankLLM response failed the permutation format contract")
        window_size = _window_size_from_prompt(invocation.prompt)
        identifiers = [int(value) for value in re.findall(extraction, response)]
        if window_size < 1 or identifiers != list(dict.fromkeys(identifiers)):
            raise ValueError("RankLLM response contains duplicate or unbounded identifiers")
        if len(identifiers) != window_size or set(identifiers) != set(range(1, window_size + 1)):
            raise ValueError("RankLLM response is not a complete window permutation")


def _ranked_permutation(result: Any, candidate_count: int) -> list[RerankResult]:
    _validate_raw_invocations(result)
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
    coordinator: BoundedSafeLiteLLM,
    request: Request,
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
