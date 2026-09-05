"""Cross-encoder rerank provider fallback chain."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Any

from ..inference import ChainExhaustedError, ModelSpec, execute_with_fallback, get_chain
from ..inference.engine import is_retryable_error
from ..models import WebSearchResult
from .models import RerankCandidate, RerankResult

logger = logging.getLogger(__name__)


class RerankResponseError(ValueError):
    """Raised when a provider returns an unsafe rerank permutation."""


@dataclass(frozen=True, slots=True)
class RerankProviderOutcome:
    """Result of running the cross-encoder rerank against the chain."""

    provider_id: str
    model: str | None
    ranked: list[RerankResult]
    error: Exception | None = None


def build_rerank_candidates(
    candidates: list[WebSearchResult],
) -> list[RerankCandidate]:
    import yaml

    rerank_candidates = []
    for index, candidate in enumerate(candidates):
        doc_dict = {
            "Title": candidate.title,
            "Snippet": candidate.snippet,
            "URL": candidate.link,
            "Domain": candidate.domain or "unknown",
            "Providers": list(candidate.providers or []),
            "ProviderCount": len(candidate.providers) if candidate.providers else 1,
        }
        if candidate.published_date:
            doc_dict["PublishedDate"] = candidate.published_date
        yaml_str = yaml.safe_dump(
            doc_dict,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        ).strip()
        rerank_candidates.append(
            RerankCandidate(
                index=index,
                document=yaml_str,
            )
        )
    return rerank_candidates


def _spec_to_provider_id(spec: ModelSpec) -> str:
    if spec.provider == "cohere":
        return "cohere_fast"
    if spec.provider == "openrouter_rerank":
        return "cohere_fast_openrouter"
    return spec.provider


def parse_rerank_response(
    payload: Any,
    *,
    candidate_count: int,
    provider: str | None = None,
) -> list[RerankResult]:
    """Parse and validate a provider response before it reaches ranking code."""
    if (
        isinstance(candidate_count, bool)
        or not isinstance(candidate_count, int)
        or candidate_count < 1
    ):
        raise RerankResponseError("candidate_count must be a positive integer")
    spec = getattr(payload, "spec", None)
    provider_name = provider or getattr(spec, "provider", "unknown")
    content = getattr(payload, "content", payload)

    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RerankResponseError(f"{provider_name} returned invalid JSON: {exc.msg}") from exc

    if not isinstance(content, list):
        raise RerankResponseError(f"{provider_name} rerank response must be a list")
    if not content:
        raise RerankResponseError(f"{provider_name} rerank response must not be empty")
    if len(content) > candidate_count:
        raise RerankResponseError(
            f"{provider_name} returned {len(content)} results for {candidate_count} candidates"
        )

    seen: set[int] = set()
    ranked: list[RerankResult] = []
    for position, item in enumerate(content):
        if not isinstance(item, dict):
            raise RerankResponseError(f"{provider_name} result {position} must be an object")
        index = item.get("index")
        score = item.get("relevance_score")
        if isinstance(index, bool) or not isinstance(index, int):
            raise RerankResponseError(f"{provider_name} result {position} has a non-integer index")
        if index < 0 or index >= candidate_count:
            raise RerankResponseError(
                f"{provider_name} result {position} index {index} is out of range"
            )
        if index in seen:
            raise RerankResponseError(
                f"{provider_name} rerank response contains duplicate index {index}"
            )
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise RerankResponseError(f"{provider_name} result {position} has a non-numeric score")
        score_float = float(score)
        if not math.isfinite(score_float):
            raise RerankResponseError(f"{provider_name} result {position} has a non-finite score")
        if not 0.0 <= score_float <= 1.0:
            raise RerankResponseError(f"{provider_name} result {position} score is outside [0, 1]")
        seen.add(index)
        ranked.append(RerankResult(index=index, relevance_score=score_float))
    return ranked


def _is_retryable_rerank_error(exc: Exception) -> bool:
    return isinstance(exc, RerankResponseError) or is_retryable_error(exc)


async def rerank_with_provider_fallback(
    query: str,
    candidates: list[WebSearchResult],
) -> RerankProviderOutcome:
    """Run cross-encoder rerank using the unified inference fallback engine."""
    if not candidates:
        return RerankProviderOutcome(provider_id="none", model=None, ranked=[])

    prepared = build_rerank_candidates(candidates)
    documents = [candidate.document for candidate in prepared]
    chain = get_chain("cross_encoder_rerank")

    try:
        exec_res = await execute_with_fallback(
            chain,
            operation="rerank_cross_encoder",
            query=query,
            documents=documents,
            top_n=len(candidates),
            is_retryable=_is_retryable_rerank_error,
            validator=lambda payload: parse_rerank_response(
                payload,
                candidate_count=len(candidates),
            ),
        )
        return RerankProviderOutcome(
            provider_id=_spec_to_provider_id(exec_res.spec),
            model=exec_res.spec.model_id,
            ranked=exec_res.payload,
        )
    except ChainExhaustedError as exc:
        last_error = exc.errors[-1][1] if exc.errors else exc
        return RerankProviderOutcome(
            provider_id="chain",
            model=None,
            ranked=[],
            error=last_error,
        )
