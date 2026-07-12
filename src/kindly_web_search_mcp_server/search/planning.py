"""Deterministic request planning for the shared web-search service."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable

from ..llm.router import build_worker_router
from ..telemetry.spans import get_tracer
from .brave import spellcheck_brave, suggest_brave_queries
from .contracts import ContractModel, ProviderTarget, QueryBranch, SearchPlan, SearchRun
from .intent_policy import resolve_intent_policy
from .keyword_extract import extract_must_keep_terms
from .normalize import normalize_query
from .provider_registry import PROVIDER_DEFINITIONS, select_provider_names
from .understanding.resolver import resolve_query_understanding

LOGGER = logging.getLogger(__name__)
_ENRICHMENT_TIMEOUT_SECONDS = 10.0
_TARGET_PRIORITY = (
    ProviderTarget.ORIGINAL,
    ProviderTarget.KEYWORD,
    ProviderTarget.NEURAL,
    ProviderTarget.COMMUNITY,
)


class _RewriteBranches(ContractModel):
    branches: tuple[QueryBranch, ...]


async def _bounded(awaitable: Awaitable[Any]) -> Any:
    return await asyncio.wait_for(awaitable, timeout=_ENRICHMENT_TIMEOUT_SECONDS)


def _stable_terms(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = normalize_query(value)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
    return tuple(output)


def _suggestions(payload: object) -> tuple[str, ...]:
    if not isinstance(payload, dict):
        return ()
    values = payload.get("queries") or payload.get("results") or payload.get("suggestions") or ()
    if not isinstance(values, (list, tuple)):
        return ()
    output: list[str] = []
    for value in values:
        if isinstance(value, str):
            output.append(value)
        elif isinstance(value, dict) and isinstance(value.get("query"), str):
            output.append(value["query"])
    return _stable_terms(output)


async def _rewrite_branches(
    *,
    query: str,
    research_goal: str,
    terms: tuple[str, ...],
    suggestions: tuple[str, ...],
    correction: str | None,
    num_results: int,
) -> tuple[tuple[QueryBranch, ...], dict[str, Any]]:
    evidence = {
        "must_keep_terms": terms,
        "suggestions": suggestions,
        "spell_correction": correction,
    }
    started = time.monotonic()
    generation = await build_worker_router().complete_json(
        messages=[
            {
                "role": "system",
                "content": (
                    "Return at most five search branches as JSON {branches:[...]}. "
                    "Each branch must contain target (keyword, neural, or community), "
                    "query, why, weight, must_keep_terms, and max_results. Preserve "
                    "the user's operators and mandatory terms."
                ),
            },
            {
                "role": "user",
                "content": f"query={query!r}\nresearch_goal={research_goal!r}\nevidence={evidence!r}",
            },
        ],
        response_model=_RewriteBranches,
        timeout_seconds=20.0,
    )
    parsed = _RewriteBranches.model_validate_json(generation.content)
    branches = tuple(
        branch.model_copy(update={"max_results": num_results})
        for branch in parsed.branches[:5]
        if normalize_query(branch.query)
    )
    metadata = {
        "model": generation.model_used,
        "input_tokens": generation.input_tokens,
        "output_tokens": generation.output_tokens,
        "latency_ms": (time.monotonic() - started) * 1000.0,
        "branch_count": len(branches),
        "prompt": f"query={query!r}\nresearch_goal={research_goal!r}",
    }
    return branches, metadata

def _keyword_query(base: str, terms: tuple[str, ...]) -> str:
    folded = base.casefold()
    additions = [term for term in terms[:4] if term.casefold() not in folded]
    return normalize_query(" ".join((base, *additions)))


def _dedupe_and_cover(
    branches: list[QueryBranch], selected_provider_names: tuple[str, ...], num_results: int
) -> tuple[QueryBranch, ...]:
    represented_targets = {
        target for name in selected_provider_names for target in PROVIDER_DEFINITIONS[name].targets
    }
    existing_targets = {branch.target for branch in branches}
    original_query = branches[0].query
    for target in _TARGET_PRIORITY:
        if target in represented_targets and target not in existing_targets:
            branches.append(
                QueryBranch(
                    target=target,
                    query=original_query,
                    why="selected-provider target coverage",
                    max_results=num_results,
                )
            )
            existing_targets.add(target)
    seen: set[tuple[ProviderTarget, str]] = set()
    output: list[QueryBranch] = []
    for branch in branches:
        key = (branch.target, normalize_query(branch.query).casefold())
        if key in seen:
            continue
        seen.add(key)
        output.append(branch)
    return tuple(output[:10])


async def plan_search(run: SearchRun) -> SearchPlan:
    tracer = get_tracer()
    started = time.monotonic()
    with tracer.start_as_current_span("search.plan") as span:
        span.set_attribute("search.query", run.request.query)
        span.set_attribute("search.rewrite_enabled", run.request.rewrite)
        request = run.request
        normalized_query = normalize_query(request.query)
        understanding = await resolve_query_understanding(
            query=normalized_query,
            research_goal=request.research_goal,
            session_id=run.session_id,
            run_key=run.run_key,
        )
        policy = resolve_intent_policy(understanding.intent)
        enrichment = await asyncio.gather(
            _bounded(extract_must_keep_terms(request.research_goal)),
            _bounded(suggest_brave_queries(normalized_query, http_client=run.http_client)),
            _bounded(spellcheck_brave(normalized_query, http_client=run.http_client)),
            return_exceptions=True,
        )
        terms = _stable_terms(enrichment[0]) if isinstance(enrichment[0], list) else ()
        suggestions = _suggestions(enrichment[1])
        correction = enrichment[2] if isinstance(enrichment[2], str) else None
        selected = select_provider_names(policy.specialized_providers)
        dc = run.diagnostics
        dc.intent = str(understanding.intent)
        dc.understanding_confidence = understanding.confidence
        dc.enrichment = {
            "rake_terms": list(terms),
            "brave_autosuggest": list(suggestions),
            "brave_spellcheck": correction,
        }
        branches = [
            QueryBranch(
                target=ProviderTarget.ORIGINAL,
                query=normalized_query,
                why="original normalized query",
                must_keep_terms=terms,
                max_results=request.num_results,
            )
        ]
        keyword_query = _keyword_query(
            normalize_query(correction) if correction else normalized_query,
            terms,
        )
        if request.rewrite:
            try:
                rewrite_branches, rewrite_meta = await _rewrite_branches(
                    query=normalized_query,
                    research_goal=request.research_goal,
                    terms=terms,
                    suggestions=suggestions,
                    correction=correction,
                    num_results=request.num_results,
                )
                branches.extend(rewrite_branches)
                dc.rewrite_metadata = rewrite_meta
            except Exception as exc:
                LOGGER.warning(
                    "Query rewrite failed; using target coverage: %s", type(exc).__name__
                )
                dc.rewrite_metadata = {"error": type(exc).__name__}
        else:
            if keyword_query:
                branches.append(
                    QueryBranch(
                        target=ProviderTarget.KEYWORD,
                        query=keyword_query,
                        why="deterministic spellcheck and keyword enrichment",
                        must_keep_terms=terms,
                        max_results=request.num_results,
                    )
                )
            for suggestion in suggestions:
                if suggestion.casefold() not in {
                    normalized_query.casefold(),
                    keyword_query.casefold(),
                }:
                    branches.append(
                        QueryBranch(
                            target=ProviderTarget.KEYWORD,
                            query=suggestion,
                            why="Brave Autosuggest",
                            must_keep_terms=terms,
                            max_results=request.num_results,
                        )
                    )
                    break
        branches_tuple = _dedupe_and_cover(branches, selected, request.num_results)
        plan = SearchPlan.create(
            normalized_query=normalized_query,
            relevance_query=f"{normalized_query}\n{request.research_goal}",
            understanding=understanding,
            options=request.options,
            selected_provider_names=selected,
            provider_arguments=policy.provider_arguments,
            branches=branches_tuple,
            policy_version=policy.policy_version,
        )
        run.plan = plan
        dc.phase_timings["search.plan"] = (time.monotonic() - started) * 1000.0
        span.set_attribute("search.branch_count", len(branches_tuple))
        span.set_attribute("search.intent", dc.intent or "")
        return plan
