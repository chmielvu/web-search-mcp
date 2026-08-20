"""Cross-encoder rerank provider fallback chain.

Routes through the unified ``kindly_web_search_mcp_server.inference`` fallback engine
and dynamic model catalog while preserving legacy output contracts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..inference import ChainExhaustedError, ModelSpec, execute_with_fallback, get_chain
from ..models import WebSearchResult
from .models import RerankCandidate, RerankResult

logger = logging.getLogger(__name__)


# Legacy alias maintained for backwards compatibility
_PROVIDER_CHAIN: tuple[str, ...] = ("cohere_fast", "cohere_fast_openrouter", "voyage")


@dataclass(frozen=True, slots=True)
class RerankProviderOutcome:
    """Result of running the cross-encoder rerank against the chain."""

    provider_id: str
    model: str | None
    ranked: list[RerankResult]
    ordered_candidates: list[WebSearchResult]
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
            "ProviderCount": candidate.provider_count or 1,
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


async def _parse_rerank_result(spec: ModelSpec, gen) -> list[tuple[int, float]]:
    """Parse an LLMGeneration content back into rerank results."""
    content = gen.content
    if content.startswith("["):
        import json

        try:
            raw = json.loads(content)
            return [(item["index"], item["relevance_score"]) for item in raw]
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    raise ValueError(f"Cannot parse rerank result from {spec.provider}: {content[:200]}")


async def rerank_with_provider_fallback(
    query: str,
    candidates: list[WebSearchResult],
) -> RerankProviderOutcome:
    """Run cross-encoder rerank using the unified inference fallback engine."""
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
        )
        gen = exec_res.payload
        ranked_list = await _parse_rerank_result(exec_res.spec, gen)
        ranked = [RerankResult(index=index, score=score) for index, score in ranked_list]
        provider_id = _spec_to_provider_id(exec_res.spec)
        return RerankProviderOutcome(
            provider_id=provider_id,
            model=exec_res.spec.model_id,
            ranked=ranked,
            ordered_candidates=[candidates[item.index] for item in ranked],
        )
    except ChainExhaustedError as exc:
        last_error = exc.errors[-1][1] if exc.errors else exc
        return RerankProviderOutcome(
            provider_id="chain",
            model=None,
            ranked=[],
            ordered_candidates=candidates,
            error=last_error,
        )


def _default_model_for(provider_id: str) -> str | None:
    chain = get_chain("cross_encoder_rerank")
    for spec in chain.models:
        if _spec_to_provider_id(spec) == provider_id:
            return spec.model_id
    return None
