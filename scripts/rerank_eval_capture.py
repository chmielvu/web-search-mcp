"""Live retrieval capture and one-time pre-diversity window materialization."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence
from unittest.mock import patch
import uuid

import httpx

from kindly_web_search_mcp_server.embeddings.hf_inference import embed_query
from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.rerank.bm25 import score_candidates
from kindly_web_search_mcp_server.rerank.core import rerank_results
from kindly_web_search_mcp_server.search.blocklist import filter_blocked_results
from kindly_web_search_mcp_server.search.contracts import SearchRun, WebSearchRequest
from kindly_web_search_mcp_server.search.merge import reciprocal_rank_fusion
from kindly_web_search_mcp_server.search.normalize import canonicalize_url
from kindly_web_search_mcp_server.search.planning import plan_search
from kindly_web_search_mcp_server.search.retrieval import retrieve_branches
from kindly_web_search_mcp_server.settings import settings

from rerank_eval_common import hybrid_rrf


def _document(result: WebSearchResult) -> dict[str, Any]:
    return {
        "title": result.title,
        "link": result.link,
        "snippet": result.snippet,
        "domain": result.domain or "",
        "providers": list(result.providers or []),
        "provider_count": result.provider_count or 1,
    }


def _result(document: dict[str, Any], score: float) -> WebSearchResult:
    return WebSearchResult(
        title=document["title"],
        link=document["link"],
        snippet=document["snippet"],
        domain=document["domain"],
        providers=document["providers"],
        provider_count=document["provider_count"],
        score=score,
        hybrid_rrf_score=score,
    )


def _capture_record(case: dict[str, Any], run: SearchRun) -> dict[str, Any]:
    provider_lists = [
        filter_blocked_results(list(ranked.results))
        for outcome in run.outcomes
        for ranked in outcome.provider_ranked_results
        if ranked.results
    ]
    provider_lists = [ranking for ranking in provider_lists if ranking]
    consensus = reciprocal_rank_fusion(provider_lists, k=60)
    consensus_results = [result for result, _ in consensus]
    scores = score_candidates(
        run.plan.relevance_query if run.plan else case["query"],
        [f"{result.title}\n{result.snippet}"[:4000] for result in consensus_results],
    )
    documents = {canonicalize_url(result.link): _document(result) for result in consensus_results}
    return {
        "id": case["id"],
        "query": case["query"],
        "research_goal": case["research_goal"],
        "intent": str(run.plan.understanding.intent) if run.plan else "general",
        "provider_rankings": [
            [canonicalize_url(result.link) for result in ranking] for ranking in provider_lists
        ],
        "bm25_scores": {
            canonicalize_url(result.link): score
            for result, score in zip(consensus_results, scores, strict=True)
        },
        "documents": documents,
    }


async def capture_fusion_inputs(
    corpus: Sequence[dict[str, Any]],
    *,
    max_in_flight: int = 5,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(max_in_flight)
    async with httpx.AsyncClient(timeout=90.0) as client:

        async def capture_one(case: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                request = WebSearchRequest(
                    query=case["query"],
                    research_goal=case["research_goal"],
                    num_results=50,
                    rewrite=True,
                )
                run = SearchRun(request=request, http_client=client, run_key=str(uuid.uuid4()))
                plan = await plan_search(run)
                embedding_task = None
                if any("qdrant" in branch.provider_names for branch in plan.branches):
                    embedding_task = asyncio.create_task(embed_query(plan.relevance_query))
                try:
                    await retrieve_branches(run, embedding_task=embedding_task)
                    return _capture_record(case, run)
                finally:
                    if embedding_task is not None and not embedding_task.done():
                        embedding_task.cancel()
                        await asyncio.gather(embedding_task, return_exceptions=True)

        return list(await asyncio.gather(*(capture_one(case) for case in corpus)))


async def materialize_diversity_windows(
    records: Sequence[dict[str, Any]],
    *,
    selected_k: int,
    thresholds: dict[str, float],
    cross_threshold_checksum: str,
) -> list[dict[str, Any]]:
    materialized: list[dict[str, Any]] = []
    with (
        patch.object(settings, "rerank_score_thresholds_json", json.dumps(thresholds)),
        patch.object(settings, "diversity_similarity_threshold", 1.0),
        patch.object(settings, "diversity_max_per_host", 30),
    ):
        for record in records:
            order, hybrid_scores = hybrid_rrf(record, selected_k)
            candidates = [
                _result(record["documents"][item_id], hybrid_scores[item_id]) for item_id in order
            ]
            output = await rerank_results(
                record["query"],
                candidates,
                top_k=len(candidates),
                research_goal=record["research_goal"],
                query_type_hint=record["intent"],
                run_key=f"rerank-tuning-{record['id']}",
                ab_overrides={"diversity_weight": 1.0},
            )
            window = output.results[: settings.rerank_llm_candidate_limit]
            context = output.embedding_context
            if context is None:
                raise RuntimeError(f"{record['id']} did not produce diversity embeddings")
            window_rows = []
            for candidate in window:
                embedding = context.find(candidate.link)
                if embedding is None:
                    raise RuntimeError(f"{record['id']} lacks embedding for {candidate.link}")
                window_rows.append(
                    {**_document(candidate), "url": candidate.link, "embedding": embedding.dense}
                )
            materialized.append(
                {
                    "id": record["id"],
                    "query": record["query"],
                    "research_goal": record["research_goal"],
                    "intent": record["intent"],
                    "selected_rrf_k": selected_k,
                    "cross_threshold_checksum": cross_threshold_checksum,
                    "rerank_provider": output.provider,
                    "rerank_model": output.model,
                    "window": window_rows,
                }
            )
    return materialized


def file_checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
