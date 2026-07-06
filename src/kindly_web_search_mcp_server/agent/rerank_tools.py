from __future__ import annotations

from typing import Any

from langchain.tools import tool

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.rerank.core import rerank_results

from .models import RerankCandidatesInput


def _to_result(candidate: Any) -> WebSearchResult:
    if isinstance(candidate, WebSearchResult):
        return candidate
    if hasattr(candidate, "model_dump"):
        candidate = candidate.model_dump()
    if not isinstance(candidate, dict):
        raise TypeError("Candidate must be a dict-like search result.")
    return WebSearchResult(
        title=str(candidate.get("title", "")).strip(),
        link=str(candidate.get("link", "")).strip(),
        snippet=str(candidate.get("snippet", "")).strip(),
        domain=candidate.get("domain"),
        published_date=candidate.get("published_date"),
        providers=[str(candidate["provider"])] if candidate.get("provider") else None,
        raw_score=candidate.get("raw_score"),
        score=candidate.get("score"),
    )


async def _rerank_candidates(
    query: str,
    candidates: list[dict[str, Any]],
    top_k: int,
) -> dict[str, Any]:
    result_models = [_to_result(candidate) for candidate in candidates]
    reranked = await rerank_results(query, result_models, top_k=top_k)
    ranked = reranked.results
    return {
        "query": query,
        "top_k": top_k,
        "candidate_count": len(candidates),
        "results": [item.model_dump(exclude_none=True) for item in ranked],
    }


rerank_candidates = tool(
    "rerank_candidates",
    args_schema=RerankCandidatesInput,
    description=(
        "Re-rank a candidate pool after multiple search calls. Use when you have more "
        "than a few competing URLs or need to reduce duplicate/noisy candidates."
    ),
)(_rerank_candidates)


def get_rerank_tools() -> list[Any]:
    return [rerank_candidates]
