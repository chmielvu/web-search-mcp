"""Rerank engine registry and fallback execution."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..models import WebSearchResult
from ..settings import settings
from .cohere import cohere_rerank
from .models import RerankCandidate, RerankEngine, RerankResult
from .openrouter import openrouter_cohere_rerank
from .voyage import voyage_rerank

logger = logging.getLogger(__name__)


SUPPORTED_ENGINE_IDS = {
    "none",
    "cohere_fast",
    "cohere_fast_openrouter",
    "voyage",
    "local_baseline",
}


@dataclass(frozen=True, slots=True)
class RerankEngineOutcome:
    """Result of running provider reranking behind the engine abstraction."""

    engine_id: str
    model: str | None
    ranked: list[RerankResult]
    ordered_candidates: list[WebSearchResult]
    error: Exception | None = None


class NoneRerankEngine:
    engine_id = "none"

    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        *,
        model: str | None = None,
        instruction: str | None = None,
    ) -> list[RerankResult]:
        return []


class LocalBaselineRerankEngine(NoneRerankEngine):
    engine_id = "local_baseline"
    default_model = "ms-marco-MiniLM-L-12-v2"

    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        *,
        model: str | None = None,
        instruction: str | None = None,
    ) -> list[RerankResult]:
        try:
            from flashrank import Ranker, RerankRequest  # type: ignore
        except Exception:
            logger.debug("flashrank not installed; local_baseline falls back to merged order")
            return []

        model_name = model or self.default_model
        try:
            # Ranker may download on first use; keep timeout friendly
            ranker = Ranker(model_name=model_name, cache_dir=None)
            passages = [
                {"id": i, "text": c.document, "meta": {"index": c.index}}
                for i, c in enumerate(candidates)
            ]
            req = RerankRequest(query=query, passages=passages)
            # flashrank rerank is sync
            import asyncio
            results = await asyncio.to_thread(ranker.rerank, req)
            out: list[RerankResult] = []
            for r in results or []:
                idx = r.get("meta", {}).get("index", r.get("id", 0))
                score = float(r.get("score", 0.0))
                out.append(RerankResult(index=int(idx), score=score))
            return out
        except Exception as exc:
            logger.warning("local_baseline flashrank failed: %s; preserving order", exc)
            return []


class CohereFastRerankEngine:
    engine_id = "cohere_fast"

    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        *,
        model: str | None = None,
        instruction: str | None = None,
    ) -> list[RerankResult]:
        ranked = await cohere_rerank(
            query,
            [candidate.document for candidate in candidates],
            timeout=settings.cohere_rerank_timeout,
            api_key=settings.cohere_api_key or None,
            model=model or settings.cohere_rerank_model,
            instruction=instruction,
            base_url=settings.cohere_rerank_base_url,
        )
        return [RerankResult(index=index, score=score) for index, score in ranked]


class CohereFastOpenRouterRerankEngine:
    engine_id = "cohere_fast_openrouter"

    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        *,
        model: str | None = None,
        instruction: str | None = None,
    ) -> list[RerankResult]:
        ranked = await openrouter_cohere_rerank(
            query,
            [candidate.document for candidate in candidates],
            timeout=settings.openrouter_rerank_timeout,
            api_key=settings.openrouter_api_key or None,
            model=model or settings.openrouter_rerank_model,
            instruction=instruction,
            base_url=settings.openrouter_rerank_base_url,
        )
        return [RerankResult(index=index, score=score) for index, score in ranked]


class VoyageRerankEngine:
    engine_id = "voyage"

    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        *,
        model: str | None = None,
        instruction: str | None = None,
    ) -> list[RerankResult]:
        ranked = await voyage_rerank(
            query,
            [candidate.document for candidate in candidates],
            timeout=30.0,
            api_key=settings.voyage_api_key or None,
            model=model or settings.voyage_rerank_model,
            instruction=instruction,
        )
        return [RerankResult(index=index, score=score) for index, score in ranked]


def get_rerank_engine(engine_id: str) -> RerankEngine:
    normalized = engine_id.strip().lower()
    if normalized == "none":
        return NoneRerankEngine()
    if normalized == "cohere_fast":
        return CohereFastRerankEngine()
    if normalized == "cohere_fast_openrouter":
        return CohereFastOpenRouterRerankEngine()
    if normalized == "voyage":
        return VoyageRerankEngine()
    if normalized == "local_baseline":
        return LocalBaselineRerankEngine()
    raise ValueError(f"Unsupported rerank provider: {normalized}")


def get_default_model(engine_id: str) -> str | None:
    normalized = engine_id.strip().lower()
    if normalized == "cohere_fast":
        return settings.cohere_rerank_model
    if normalized == "cohere_fast_openrouter":
        return settings.openrouter_rerank_model
    if normalized == "voyage":
        return settings.voyage_rerank_model
    if normalized == "local_baseline":
        return LocalBaselineRerankEngine.default_model
    return None


def build_rerank_candidates(
    candidates: list[WebSearchResult],
) -> list[RerankCandidate]:
    return [
        RerankCandidate(
            index=index,
            document=(
                f"Title: {candidate.title}\n"
                f"Snippet: {candidate.snippet}\n"
                f"URL: {candidate.link}\n"
                f"Domain: {candidate.domain or 'unknown'}\n"
                f"Providers: {', '.join(candidate.providers or [])}\n"
                f"ProviderCount: {candidate.provider_count or 1}"
            ),
        )
        for index, candidate in enumerate(candidates)
    ]


# The single fallback chain: cohere_fast -> cohere_fast_openrouter -> voyage.
# Used regardless of which engine is configured — always try in this order.
_FALLBACK_CHAIN = ["cohere_fast", "cohere_fast_openrouter", "voyage"]


def _fallback_order(engine_id: str) -> list[str]:
    normalized = engine_id.strip().lower()
    if normalized in {"none", "local_baseline"}:
        return [normalized]
    return list(_FALLBACK_CHAIN)


async def rerank_with_engine_fallback(
    query: str,
    candidates: list[WebSearchResult],
    *,
    engine_id: str,
    model: str | None = None,
    instruction: str | None = None,
) -> RerankEngineOutcome:
    prepared = build_rerank_candidates(candidates)
    backend_error: Exception | None = None

    for candidate_engine_id in _fallback_order(engine_id):
        engine = get_rerank_engine(candidate_engine_id)
        resolved_model = model if candidate_engine_id == engine_id else None
        resolved_model = resolved_model or get_default_model(candidate_engine_id)
        try:
            supports_instruction = candidate_engine_id in {
                "voyage",
                "cohere_fast",
                "cohere_fast_openrouter",
            }
            ranked = await engine.rerank(
                query,
                prepared,
                model=resolved_model,
                instruction=instruction if supports_instruction else None,
            )
        except Exception as exc:
            backend_error = exc
            logger.warning(
                "%s rerank failed: %s: %s, trying fallback provider",
                candidate_engine_id.capitalize(),
                type(exc).__name__,
                exc,
            )
            continue

        if not ranked:
            return RerankEngineOutcome(
                engine_id="none" if candidate_engine_id == "none" else candidate_engine_id,
                model=resolved_model,
                ranked=[],
                ordered_candidates=candidates,
            )

        return RerankEngineOutcome(
            engine_id=candidate_engine_id,
            model=resolved_model,
            ranked=ranked,
            ordered_candidates=[candidates[item.index] for item in ranked],
        )

    return RerankEngineOutcome(
        engine_id="none",
        model=None,
        ranked=[],
        ordered_candidates=candidates,
        error=backend_error,
    )
