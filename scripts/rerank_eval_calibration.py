"""Measured provider/model cross-score calibration for frozen borderline pairs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
import math
import statistics
from typing import Any

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.prompts.rerank import build_cross_encoder_query
from kindly_web_search_mcp_server.rerank.cohere import cohere_rerank
from kindly_web_search_mcp_server.rerank.openrouter import openrouter_cohere_rerank
from kindly_web_search_mcp_server.rerank.providers import build_rerank_candidates
from kindly_web_search_mcp_server.settings import settings

from rerank_eval_common import validate_borderline_fixture

RouteCall = Callable[[str, list[str]], Awaitable[list[tuple[int, float]]]]


def _fixture_result(row: dict[str, Any]) -> WebSearchResult:
    candidate = row["candidate"]
    return WebSearchResult(
        title=candidate["title"],
        link=candidate["url"],
        snippet=candidate["snippet"],
        domain=candidate["domain"],
        providers=candidate["providers"],
        provider_count=candidate["provider_count"],
    )


async def _direct_cohere(query: str, documents: list[str]) -> list[tuple[int, float]]:
    return await cohere_rerank(
        query,
        documents,
        api_key=settings.cohere_api_key or None,
        model=settings.cohere_rerank_model,
        top_n=1,
        timeout=settings.cohere_rerank_timeout,
        base_url=settings.cohere_rerank_base_url,
    )


async def _openrouter_cohere(query: str, documents: list[str]) -> list[tuple[int, float]]:
    return await openrouter_cohere_rerank(
        query,
        documents,
        api_key=settings.openrouter_api_key or None,
        model=settings.openrouter_rerank_model,
        top_n=1,
        timeout=settings.openrouter_rerank_timeout,
        base_url=settings.openrouter_rerank_base_url,
    )


def configured_routes() -> dict[str, tuple[str, RouteCall]]:
    return {
        "cohere_fast": (settings.cohere_rerank_model, _direct_cohere),
        "cohere_fast_openrouter": (settings.openrouter_rerank_model, _openrouter_cohere),
    }


async def _score_route(
    records: Sequence[dict[str, Any]],
    call: RouteCall,
) -> tuple[list[float], list[dict[str, str]]]:
    semaphore = asyncio.Semaphore(4)

    async def score_one(row: dict[str, Any]) -> tuple[float | None, dict[str, str] | None]:
        query = build_cross_encoder_query(
            row["query"],
            row["intent"],
            row["research_goal"],
        )
        document = build_rerank_candidates([_fixture_result(row)])[0].document
        try:
            async with semaphore:
                ranked = await call(query, [document])
            if len(ranked) != 1 or ranked[0][0] != 0:
                raise ValueError("calibration response is not a complete one-document ranking")
            score = float(ranked[0][1])
            if not math.isfinite(score) or not 0.0 <= score <= 1.0:
                raise ValueError("calibration score is not finite in [0,1]")
            return score, None
        except Exception as exc:
            return None, {"id": str(row["id"]), "error": f"{type(exc).__name__}: {exc}"}

    outcomes = await asyncio.gather(*(score_one(record) for record in records))
    return (
        [score for score, _ in outcomes if score is not None],
        [failure for _, failure in outcomes if failure is not None],
    )


async def calibrate_cross_thresholds(
    records: Sequence[dict[str, Any]],
    *,
    route_calls: dict[str, tuple[str, RouteCall]] | None = None,
) -> dict[str, Any]:
    fixture = validate_borderline_fixture(records)
    routes = route_calls if route_calls is not None else configured_routes()
    thresholds: dict[str, float] = {}
    route_results: dict[str, Any] = {}
    for provider_id, (model, call) in routes.items():
        scores, failures = await _score_route(records, call)
        threshold_key = f"{provider_id}:{model}"
        enough = len(scores) >= 30
        if enough:
            thresholds[threshold_key] = statistics.fmean(scores)
        route_results[threshold_key] = {
            "provider_id": provider_id,
            "model": model,
            "status": "calibrated" if enough else "insufficient_samples",
            "sample_count": len(scores),
            "cutoff": thresholds.get(threshold_key),
            "mean": statistics.fmean(scores) if scores else None,
            "standard_deviation": statistics.pstdev(scores) if scores else None,
            "minimum": min(scores) if scores else None,
            "maximum": max(scores) if scores else None,
            "failures": failures,
        }
    return {
        "schema_version": 1,
        **fixture,
        "method": "arithmetic_mean_of_independent_borderline_scores",
        "max_in_flight_per_route": 4,
        "thresholds": dict(sorted(thresholds.items())),
        "routes": route_results,
    }
