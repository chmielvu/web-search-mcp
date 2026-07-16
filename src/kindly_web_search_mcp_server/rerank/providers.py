"""Cross-encoder rerank provider fallback chain.

The chain is hardcoded; there is no per-provider configurability. Each
provider in the chain is a thin wrapper over its corresponding vendor
function (cohere / openrouter / voyage).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from ..models import WebSearchResult
from ..settings import settings
from .cohere import cohere_rerank
from .models import RerankCandidate, RerankResult
from .openrouter import openrouter_cohere_rerank
from .voyage import voyage_rerank

logger = logging.getLogger(__name__)


_ProviderCallable = Callable[[str, list[str]], Awaitable[list[tuple[int, float]]]]


# The single hardcoded provider chain. There is no per-provider
# configurability by design.
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


async def _call_cohere_fast(
    query: str,
    documents: list[str],
) -> list[tuple[int, float]]:
    return await cohere_rerank(
        query,
        documents,
        timeout=settings.cohere_rerank_timeout,
        api_key=settings.cohere_api_key or None,
        model=settings.cohere_rerank_model,
        base_url=settings.cohere_rerank_base_url,
    )


async def _call_cohere_fast_openrouter(
    query: str,
    documents: list[str],
) -> list[tuple[int, float]]:
    return await openrouter_cohere_rerank(
        query,
        documents,
        timeout=settings.openrouter_rerank_timeout,
        api_key=settings.openrouter_api_key or None,
        model=settings.openrouter_rerank_model,
        base_url=settings.openrouter_rerank_base_url,
    )


async def _call_voyage(
    query: str,
    documents: list[str],
) -> list[tuple[int, float]]:
    return await voyage_rerank(
        query,
        documents,
        timeout=30.0,
        api_key=settings.voyage_api_key or None,
        model=settings.voyage_rerank_model,
    )


_PROVIDER_DISPATCH: dict[str, _ProviderCallable] = {
    "cohere_fast": _call_cohere_fast,
    "cohere_fast_openrouter": _call_cohere_fast_openrouter,
    "voyage": _call_voyage,
}


async def rerank_with_provider_fallback(
    query: str,
    candidates: list[WebSearchResult],
) -> RerankProviderOutcome:
    """Run the cross-encoder rerank against the single hardcoded chain."""
    prepared = build_rerank_candidates(candidates)
    documents = [candidate.document for candidate in prepared]
    backend_error: Exception | None = None

    for provider_id in _PROVIDER_CHAIN:
        call = _PROVIDER_DISPATCH[provider_id]
        _t0 = time.time()
        try:
            ranked_pairs = await call(query, documents)
        except Exception as exc:
            backend_error = exc
            logger.warning(
                "rerank provider %s failed in %.2fs: %s: %s, trying next",
                provider_id,
                time.time() - _t0,
                type(exc).__name__,
                exc,
            )
            continue

        logger.info(
            "rerank provider %s succeeded in %.2fs (ranked=%d)",
            provider_id,
            time.time() - _t0,
            len(ranked_pairs),
        )
        ranked = [RerankResult(index=index, score=score) for index, score in ranked_pairs]
        return RerankProviderOutcome(
            provider_id=provider_id,
            model=_default_model_for(provider_id),
            ranked=ranked,
            ordered_candidates=[candidates[item.index] for item in ranked],
        )

    return RerankProviderOutcome(
        provider_id="chain",
        model=None,
        ranked=[],
        ordered_candidates=candidates,
        error=backend_error,
    )


def _default_model_for(provider_id: str) -> str | None:
    if provider_id == "cohere_fast":
        return settings.cohere_rerank_model
    if provider_id == "cohere_fast_openrouter":
        return settings.openrouter_rerank_model
    if provider_id == "voyage":
        return settings.voyage_rerank_model
    return None
