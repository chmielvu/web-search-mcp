"""Deterministic six-branch planning for the shared web-search service."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any, Awaitable, Sequence

from ..heuristics.query_features import QueryFeatures, build_query_features
from ..heuristics.text_segment import segment_query
from ..inference.router import build_worker_router
from ..prompts.query_rewrite import (
    REWRITE_PROMPT_VERSION,
    REWRITE_SYSTEM,
    REWRITE_USER,
    RewrittenQueries,
)
from ..prompts.rerank import build_relevance_query
from ..settings import settings
from ..telemetry.spans import get_tracer
from .contracts import BranchRole, QueryBranch, SearchPlan, SearchRun
from .graph_expansion import GraphExpansionDecision, expand_seed_queries
from .intent_policy import resolve_intent_policy
from .intents import SearchIntent, normalize_intent
from .keyword_extract import extract_support_terms
from .normalize import normalize_query
from .provider_registry import (
    select_paid_google_provider,
    select_provider_names,
    select_semantic_tavily_provider,
)
from .providers.brave import suggest_brave_queries
from .understanding.resolver import resolve_query_understanding

LOGGER = logging.getLogger(__name__)
_ENRICHMENT_TIMEOUT_SECONDS = 3.0

_ORIGINAL_CANDIDATES = ("ddg", "qdrant", "searxng", "degoog")
_FREE_CANDIDATES = ("ddg", "qdrant", "searxng", "degoog")
_SERP1_CANDIDATES = ("brave",)
_SERP2_CANDIDATES = ("brightdata", "serper", "search_router")
_SEMANTIC_TAVILY_CANDIDATES = ("tavily", "langsearch")
_SEMANTIC_EXA_CANDIDATES = ("exa",)

SLOT_ORDER = ("free", "serp1", "serp2", "semantic_tavily", "semantic_exa")


def _branch_names(candidates: Sequence[str], available: Sequence[str]) -> tuple[str, ...]:
    avail_set = set(available)
    return tuple(n for n in candidates if n in avail_set)


async def _bounded(awaitable: Awaitable[Any]) -> Any:
    return await asyncio.wait_for(awaitable, timeout=_ENRICHMENT_TIMEOUT_SECONDS)


def _stable_terms(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        key = value.strip()
        folded = key.casefold()
        if key and folded not in seen:
            seen.add(folded)
            output.append(key)
    return tuple(output)


def _suggestions(payload: object) -> tuple[str, ...]:
    if not isinstance(payload, dict):
        return ()
    raw = payload.get("suggestions")
    if not isinstance(raw, list):
        return ()
    output: list[str] = []
    for item in raw[:8]:
        if isinstance(item, str) and item.strip():
            output.append(" ".join(item.split()))
    return _stable_terms(output)


def _keyword_query(base: str, terms: tuple[str, ...]) -> str:
    folded = base.casefold()
    additions = [term for term in terms[:4] if term.casefold() not in folded]
    return normalize_query(" ".join((base, *additions)))


def _normalize_branch_query(query: str) -> tuple[str, bool]:
    """Normalize a rewritten slot; wordninja glue-repair is additive."""
    segmented = segment_query(query or "")
    normalized = normalize_query(segmented or query or "")
    return normalized, segmented is not None


def _branch_fallback_queries(
    features: QueryFeatures,
    *,
    terms: tuple[str, ...],
    suggestions: tuple[str, ...],
    research_goal: str,
    current_year: str,
) -> tuple[str, ...]:
    """Deterministic per-slot fallbacks: free, serp1, serp2, tavily, exa."""
    base = features.cleaned or normalize_query(features.raw)
    keyword_query = _keyword_query(base, terms)

    brave_fallback = keyword_query
    for sugg in suggestions:
        if sugg.casefold() not in {base.casefold(), keyword_query.casefold()}:
            brave_fallback = _keyword_query(sugg, terms)
            break

    fresh = bool(current_year) and features.time_sensitivity in ("recent", "current")
    suffix = f" {current_year}" if fresh else ""

    free_body = features.segmented_variants[0] if features.segmented_variants else keyword_query
    free_fb = normalize_query(f"{free_body}{suffix}")

    serp1_fb = normalize_query(f"{brave_fallback}{suffix}") or brave_fallback

    compared = features.compared_entities
    if len(compared) >= 2:
        facet = " vs ".join(compared[:3])
        serp2_fb = normalize_query(f"{facet} comparison{suffix}") or keyword_query
    else:
        serp2_fb = keyword_query

    goal = " ".join((research_goal or "").split())
    tavily_fb = normalize_query(f"{base}? {goal}") if goal else normalize_query(base)
    exa_cmp = f" comparing {compared[0]} and {compared[1]}" if len(compared) >= 2 else ""
    exa_fb = normalize_query(f"{base} authoritative sources{exa_cmp}: {goal}") or base

    return (free_fb, serp1_fb, serp2_fb, tavily_fb or base, exa_fb)


_REWRITE_CACHE: dict[str, tuple[RewrittenQueries, dict[str, Any]]] = {}
_REWRITE_CACHE_MAX_SIZE = 256


async def _rewrite_queries(
    *,
    query: str,
    seed_queries: tuple[str, ...] = (),
    research_goal: str,
    terms: tuple[str, ...],
    suggestions: tuple[str, ...],
    current_year: str,
    intent: SearchIntent = "general",
    understanding: Any | None = None,
) -> tuple[RewrittenQueries, dict[str, Any]]:
    compared_entities = _stable_terms(list(getattr(understanding, "compared_entities", None) or []))
    preserved_terms = _stable_terms(list(getattr(understanding, "preserved_terms", None) or []))
    user_content = REWRITE_USER.format(
        current_year=current_year,
        time_sensitivity=str(getattr(understanding, "time_sensitivity", "none") or "none"),
        query=query,
        seed_queries=list(seed_queries) if seed_queries else [query],
        research_goal=research_goal,
        support_terms=list(terms),
        suggestions=list(suggestions),
        compared_entities=list(compared_entities),
        should_decompose=bool(getattr(understanding, "should_decompose", False) or False),
        preserved_terms=list(preserved_terms),
    )
    cache_key = hashlib.sha256(
        f"v{REWRITE_PROMPT_VERSION}:{normalize_intent(str(intent))}:{user_content}".encode("utf-8")
    ).hexdigest()
    if cache_key in _REWRITE_CACHE:
        cached_parsed, cached_meta = _REWRITE_CACHE[cache_key]
        hit_meta = dict(cached_meta)
        hit_meta["cached"] = True
        return cached_parsed, hit_meta

    started = time.monotonic()
    generation = await build_worker_router().complete_json(
        messages=[
            {"role": "system", "content": REWRITE_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        response_model=RewrittenQueries,
        timeout_seconds=20.0,
        reasoning_effort="none",
        operation="rewrite",
    )
    parsed = RewrittenQueries.model_validate_json(generation.content)
    metadata = {
        "model": generation.model_used,
        "input_tokens": generation.input_tokens,
        "output_tokens": generation.output_tokens,
        "latency_ms": (time.monotonic() - started) * 1000.0,
        "prompt_version": REWRITE_PROMPT_VERSION,
        "prompt": f"query={query!r}\nresearch_goal={research_goal!r}\nintent={intent!r}",
    }
    if len(_REWRITE_CACHE) >= _REWRITE_CACHE_MAX_SIZE:
        _REWRITE_CACHE.clear()
    _REWRITE_CACHE[cache_key] = (parsed, metadata)
    return parsed, metadata


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
            _bounded(extract_support_terms(request.research_goal)),
            _bounded(suggest_brave_queries(normalized_query, http_client=run.http_client)),
            return_exceptions=True,
        )
        terms = _stable_terms(enrichment[0]) if isinstance(enrichment[0], list) else ()
        suggestions = _suggestions(enrichment[1])
        available = select_provider_names()
        dc = run.diagnostics
        dc.intent = str(understanding.intent)
        dc.understanding_confidence = understanding.confidence
        dc.enrichment = {
            "rake_terms": list(terms),
            "brave_autosuggest": list(suggestions),
        }

        # --- materialize the six requested provider assignments ---
        original = _branch_names(_ORIGINAL_CANDIDATES, available)
        free = _branch_names(_FREE_CANDIDATES, available)
        serp1 = _branch_names(_SERP1_CANDIDATES, available)
        serp2_name = select_paid_google_provider(available)
        serp2 = (serp2_name,) if serp2_name else ()
        semantic_tavily_name = select_semantic_tavily_provider(available)
        semantic_tavily = (semantic_tavily_name,) if semantic_tavily_name else ()
        semantic_exa = _branch_names(_SEMANTIC_EXA_CANDIDATES, available)

        # --- compute deterministic per-slot fallback queries ---
        current_year = time.strftime("%Y")
        fallback_features = build_query_features(
            normalized_query,
            understanding=understanding,
            support_terms=terms,
        )
        fallback = _branch_fallback_queries(
            fallback_features,
            terms=terms,
            suggestions=suggestions,
            research_goal=request.research_goal,
            current_year=current_year,
        )

        base_seed_queries = request.queries if request.queries else (normalized_query,)
        if request.rewrite and settings.graph_expansion_enabled:
            try:
                decision = await _bounded(
                    asyncio.to_thread(
                        expand_seed_queries,
                        normalized_query=normalized_query,
                        base_seed_queries=base_seed_queries,
                        enabled=True,
                        max_related_queries=settings.graph_expansion_max_related_queries,
                        max_age_seconds=settings.graph_expansion_max_age_seconds,
                        db_path=None,
                    )
                )
            except Exception as exc:
                decision = GraphExpansionDecision(
                    status="error",
                    generation_id=None,
                    base_seed_queries=base_seed_queries,
                    effective_seed_queries=base_seed_queries,
                    related_queries=(),
                    error_type=type(exc).__name__,
                )
        else:
            decision = expand_seed_queries(
                normalized_query=normalized_query,
                base_seed_queries=base_seed_queries,
                enabled=False,
                max_related_queries=settings.graph_expansion_max_related_queries,
                max_age_seconds=settings.graph_expansion_max_age_seconds,
            )

        graph_expansion_meta: dict[str, Any] = {
            "status": decision.status,
            "generation_id": decision.generation_id,
            "base_seed_queries": list(decision.base_seed_queries[:4]),
            "effective_seed_queries": list(decision.effective_seed_queries[:4]),
            "related_queries": list(decision.related_queries[:2]),
        }
        if decision.error_type:
            graph_expansion_meta["error_type"] = decision.error_type

        # --- resolve query texts ---
        rewritten_slots: tuple[str, ...] = ()
        if request.rewrite:
            try:
                rewrite, rewrite_meta = await _rewrite_queries(
                    query=normalized_query,
                    seed_queries=decision.effective_seed_queries,
                    research_goal=request.research_goal,
                    terms=terms,
                    suggestions=suggestions,
                    current_year=current_year,
                    intent=understanding.intent,
                    understanding=understanding,
                )
                raw_slots = {
                    "free": rewrite.free,
                    "serp1": rewrite.serp1,
                    "serp2": rewrite.serp2,
                    "semantic_tavily": rewrite.semantic_tavily,
                    "semantic_exa": rewrite.semantic_exa,
                }
                normalized_slots: dict[str, str] = {}
                glued: list[str] = []
                for name in SLOT_ORDER:
                    norm, did_glue = _normalize_branch_query(raw_slots[name])
                    normalized_slots[name] = norm
                    if did_glue:
                        glued.append(name)
                if glued:
                    rewrite_meta["segment_glued"] = glued
                rewrite_meta["branch_count"] = 6
                rewrite_meta["graph_expansion"] = graph_expansion_meta
                dc.rewrite_metadata = rewrite_meta
                # Per-slot degradation: a blank slot falls back alone.
                queries = tuple(
                    normalized_slots[name] if normalized_slots[name].strip() else fb
                    for name, fb in zip(SLOT_ORDER, fallback)
                )
                rewritten_slots = tuple(normalized_slots[name] for name in SLOT_ORDER)
            except Exception as exc:
                LOGGER.warning(
                    "Query rewrite failed; using deterministic fallback: %s", type(exc).__name__
                )
                dc.rewrite_metadata = {
                    "error": type(exc).__name__,
                    "branch_count": 6,
                    "graph_expansion": graph_expansion_meta,
                }
                queries = fallback
        else:
            dc.rewrite_metadata = {
                "branch_count": 6,
                "graph_expansion": graph_expansion_meta,
            }
            queries = fallback

        # Persist the 5 planner rewrites separately from the 6-branch
        # dispatched topology. Empty tuple when rewrite was disabled or
        # errored — the judge then writes no rewrite rows.
        rewrite_success = bool(
            request.rewrite and dc.rewrite_metadata and "error" not in dc.rewrite_metadata
        )
        rewrite_queries: tuple[str, ...] = rewritten_slots if rewrite_success else ()

        # `use_llm_why` is true when the LLM-rewrite path succeeded; in
        # every other case (rewrite disabled, rewrite errored, no
        # metadata) the branches use their deterministic fallback.
        use_llm_why = rewrite_success
        # Display name for the deterministic branch `why` string. Keys
        # must match the BranchRole values used below.
        _DETERMINISTIC_WHY = {
            BranchRole.FREE: "deterministic free query",
            BranchRole.SERP1: "deterministic SERP1 query",
            BranchRole.SERP2: "deterministic SERP2 query",
            BranchRole.SEMANTIC_TAVILY: "deterministic semantic Tavily query",
            BranchRole.SEMANTIC_EXA: "deterministic semantic Exa query",
        }

        def _why_for(role: BranchRole, llm_label: str) -> str:
            return llm_label if use_llm_why else _DETERMINISTIC_WHY[role]

        branches: tuple[QueryBranch, ...] = (
            QueryBranch(
                role=BranchRole.ORIGINAL,
                query=normalized_query,
                provider_names=original,
                why="original normalized query",
                support_terms=terms,
                max_results=request.num_results,
            ),
            QueryBranch(
                role=BranchRole.FREE,
                query=queries[0],
                provider_names=free,
                why=_why_for(BranchRole.FREE, "LLM free"),
                support_terms=terms,
                max_results=request.num_results,
            ),
            QueryBranch(
                role=BranchRole.SERP1,
                query=queries[1],
                provider_names=serp1,
                why=_why_for(BranchRole.SERP1, "LLM serp1"),
                support_terms=terms,
                max_results=request.num_results,
            ),
            QueryBranch(
                role=BranchRole.SERP2,
                query=queries[2],
                provider_names=serp2,
                why=_why_for(BranchRole.SERP2, "LLM serp2"),
                support_terms=terms,
                max_results=request.num_results,
            ),
            QueryBranch(
                role=BranchRole.SEMANTIC_TAVILY,
                query=queries[3],
                provider_names=semantic_tavily,
                why=_why_for(BranchRole.SEMANTIC_TAVILY, "LLM semantic_tavily"),
                support_terms=terms,
                max_results=request.num_results,
            ),
            QueryBranch(
                role=BranchRole.SEMANTIC_EXA,
                query=queries[4],
                provider_names=semantic_exa,
                why=_why_for(BranchRole.SEMANTIC_EXA, "LLM semantic_exa"),
                support_terms=terms,
                max_results=request.num_results,
            ),
        )

        # --- build provider arguments with engine-specific overrides ---
        provider_arguments = {
            name: dict(bundle) if isinstance(bundle, dict) else {}
            for name, bundle in (policy.provider_arguments or {}).items()
        }
        provider_arguments["gemma"] = {
            **provider_arguments.get("gemma", {}),
            "queries": list(decision.effective_seed_queries),
            "research_goal": request.research_goal,
        }
        plan = SearchPlan.create(
            normalized_query=normalized_query,
            relevance_query=build_relevance_query(normalized_query, request.research_goal),
            understanding=understanding,
            options=request.options,
            provider_arguments=provider_arguments,
            branches=branches,
            policy_version=policy.policy_version,
            rewrite_queries=rewrite_queries,
            seed_queries=decision.effective_seed_queries,
        )
        run.plan = plan
        # Collect query variant rows for funnel uplift analytics
        from ..analytics.observability_store import _canonical_result_id as _cri

        variant_rows: list[dict[str, Any]] = []
        rewrite_failed = bool(dc.rewrite_metadata and "error" in dc.rewrite_metadata)
        for index, branch in enumerate(branches):
            selected = bool(branch.query)
            executed = selected and bool(branch.provider_names)
            skip_reason = None
            if not selected:
                skip_reason = "rewrite_failed" if rewrite_failed else "empty_query"
            elif not branch.provider_names:
                skip_reason = "no_assigned_providers"
            variant_rows.append(
                {
                    "variant_id": _cri(f"{run.run_key}|variant|{index}"),
                    "run_key": run.run_key,
                    "variant_order": index,
                    "variant_role": branch.role.value,
                    "query_text": branch.query,
                    "branch_id": _cri(f"{run.run_key}|{index}"),
                    "selected": selected,
                    "executed": executed,
                    "skip_reason": skip_reason,
                }
            )
        dc.query_variant_rows = variant_rows
        dc.phase_timings["search.plan"] = (time.monotonic() - started) * 1000.0
        span.set_attribute("search.branch_count", len(branches))
        span.set_attribute("search.intent", dc.intent or "")
        return plan
