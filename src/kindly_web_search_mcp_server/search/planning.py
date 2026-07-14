"""Deterministic six-branch planning for the shared web-search service."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Sequence

from ..llm.router import build_worker_router
from ..telemetry.spans import get_tracer
from .brave import spellcheck_brave, suggest_brave_queries
from .contracts import BranchRole, ContractModel, QueryBranch, SearchPlan, SearchRun
from .intent_policy import resolve_intent_policy
from .keyword_extract import extract_support_terms
from .understanding.resolver import resolve_query_understanding
from .normalize import normalize_query
from .provider_registry import select_paid_google_provider, select_provider_names

LOGGER = logging.getLogger(__name__)
_ENRICHMENT_TIMEOUT_SECONDS = 3.0

_ORIGINAL_FREE_CANDIDATES = ("searxng", "ddg", "gemma", "degoog")
_PAID_BRAVE_CANDIDATES = ("brave",)
_PAID_GOOGLE_CANDIDATES = ("brightdata", "serper", "search_router")
_PAID_OTHER_CANDIDATES = ("brightdata_yandex", "brightdata_bing", "serpapi")
_NEURAL_CANDIDATES = ("gemma", "qdrant", "composio_llm_search")


class _RewriteQueries(ContractModel):
    paid_brave: str
    paid_google: str
    paid_other: str
    neural: str
    specialized: str


def _branch_names(candidates: Sequence[str], available: Sequence[str]) -> tuple[str, ...]:
    avail_set = set(available)
    return tuple(n for n in candidates if n in avail_set)


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


def _keyword_query(base: str, terms: tuple[str, ...]) -> str:
    folded = base.casefold()
    additions = [term for term in terms[:4] if term.casefold() not in folded]
    return normalize_query(" ".join((base, *additions)))


async def _rewrite_queries(
    *,
    query: str,
    research_goal: str,
    terms: tuple[str, ...],
    suggestions: tuple[str, ...],
    correction: str | None,
) -> tuple[_RewriteQueries, dict[str, Any]]:
    evidence = {
        "support_terms": list(terms),
        "suggestions": list(suggestions),
        "spell_correction": correction,
    }
    started = time.monotonic()
    generation = await build_worker_router().complete_json(
        messages=[
            {
                "role": "system",
                "content": (
                    "Return five role-specific search queries as JSON. "
                    "Fields: paid_brave, paid_google, paid_other, neural, specialized. "
                    "Each field is a natural-language search query string. "
                    "paid_brave is for Brave Search. paid_google is for Google/BrightData. "
                    "paid_other is for Bing/Yandex/Baidu. neural is for semantic/vector search. "
                    "specialized is for intent-specific providers. "
                    "Use the support_terms, suggestions, and spell_correction as evidence. "
                    "Preserve the user's operators and mandatory terms."
                ),
            },
            {
                "role": "user",
                "content": f"query={query!r}\nresearch_goal={research_goal!r}\nevidence={evidence!r}",
            },
        ],
        response_model=_RewriteQueries,
        timeout_seconds=20.0,
    )
    parsed = _RewriteQueries.model_validate_json(generation.content)
    metadata = {
        "model": generation.model_used,
        "input_tokens": generation.input_tokens,
        "output_tokens": generation.output_tokens,
        "latency_ms": (time.monotonic() - started) * 1000.0,
        "prompt": f"query={query!r}\nresearch_goal={research_goal!r}",
    }
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
            _bounded(spellcheck_brave(normalized_query, http_client=run.http_client)),
            return_exceptions=True,
        )
        terms = _stable_terms(enrichment[0]) if isinstance(enrichment[0], list) else ()
        suggestions = _suggestions(enrichment[1])
        correction = enrichment[2] if isinstance(enrichment[2], str) else None
        available = select_provider_names(policy.specialized_providers)
        dc = run.diagnostics
        dc.intent = str(understanding.intent)
        dc.understanding_confidence = understanding.confidence
        dc.enrichment = {
            "rake_terms": list(terms),
            "brave_autosuggest": list(suggestions),
            "brave_spellcheck": correction,
        }

        # --- materialize six independent allowlists ---
        original_free = _branch_names(_ORIGINAL_FREE_CANDIDATES, available)
        paid_brave = _branch_names(_PAID_BRAVE_CANDIDATES, available)
        paid_google_name = select_paid_google_provider(available)
        paid_google = (paid_google_name,) if paid_google_name else ()
        paid_other = _branch_names(_PAID_OTHER_CANDIDATES, available)
        neural = _branch_names(_NEURAL_CANDIDATES, available)
        specialized = _branch_names(policy.specialized_providers, available)

        # --- compute deterministic fallback queries ---
        keyword_base = normalize_query(correction) if correction else normalized_query
        keyword_query = _keyword_query(keyword_base, terms)
        brave_fallback = keyword_query
        for sugg in suggestions:
            if sugg.casefold() not in {normalized_query.casefold(), keyword_query.casefold()}:
                brave_fallback = _keyword_query(sugg, terms)
                break

        fallback = (brave_fallback, keyword_query, keyword_query, normalized_query, normalized_query)

        # --- resolve query texts ---
        if request.rewrite:
            try:
                rewrite, rewrite_meta = await _rewrite_queries(
                    query=normalized_query,
                    research_goal=request.research_goal,
                    terms=terms,
                    suggestions=suggestions,
                    correction=correction,
                )
                rewrite_meta["branch_count"] = 6
                dc.rewrite_metadata = rewrite_meta
                queries = (
                    normalize_query(rewrite.paid_brave),
                    normalize_query(rewrite.paid_google),
                    normalize_query(rewrite.paid_other),
                    normalize_query(rewrite.neural),
                    normalize_query(rewrite.specialized),
                )
            except Exception as exc:
                LOGGER.warning("Query rewrite failed; using deterministic fallback: %s", type(exc).__name__)
                dc.rewrite_metadata = {"error": type(exc).__name__, "branch_count": 6}
                queries = fallback
        else:
            dc.rewrite_metadata = {"branch_count": 6}
            queries = fallback

        # --- build six branches in fixed role order ---
        branches: tuple[QueryBranch, ...] = (
            QueryBranch(
                role=BranchRole.ORIGINAL_FREE,
                query=normalized_query,
                provider_names=original_free,
                why="original normalized query",
                support_terms=terms,
                max_results=request.num_results,
            ),
            QueryBranch(
                role=BranchRole.PAID_BRAVE,
                query=queries[0],
                provider_names=paid_brave,
                why="LLM paid_brave" if request.rewrite and dc.rewrite_metadata and "error" not in dc.rewrite_metadata else "deterministic Brave query",
                support_terms=terms,
                max_results=request.num_results,
            ),
            QueryBranch(
                role=BranchRole.PAID_GOOGLE,
                query=queries[1],
                provider_names=paid_google,
                why="LLM paid_google" if request.rewrite and dc.rewrite_metadata and "error" not in dc.rewrite_metadata else "deterministic Google query",
                support_terms=terms,
                max_results=request.num_results,
            ),
            QueryBranch(
                role=BranchRole.PAID_OTHER,
                query=queries[2],
                provider_names=paid_other,
                why="LLM paid_other" if request.rewrite and dc.rewrite_metadata and "error" not in dc.rewrite_metadata else "deterministic paid-other query",
                support_terms=terms,
                max_results=request.num_results,
            ),
            QueryBranch(
                role=BranchRole.NEURAL,
                query=queries[3],
                provider_names=neural,
                why="LLM neural" if request.rewrite and dc.rewrite_metadata and "error" not in dc.rewrite_metadata else "deterministic neural query",
                support_terms=terms,
                max_results=request.num_results,
            ),
            QueryBranch(
                role=BranchRole.SPECIALIZED,
                query=queries[4],
                provider_names=specialized,
                why="LLM specialized" if request.rewrite and dc.rewrite_metadata and "error" not in dc.rewrite_metadata else "deterministic specialized query",
                support_terms=terms,
                max_results=request.num_results,
            ),
        )

        # --- build provider arguments with engine-specific overrides ---
        provider_arguments = {
            name: dict(bundle) if isinstance(bundle, dict) else {}
            for name, bundle in (policy.provider_arguments or {}).items()
        }
        brightdata_base = provider_arguments.get("brightdata", {})
        provider_arguments["brightdata_yandex"] = {
            **brightdata_base,
            "provider_name": "brightdata_yandex",
            "language": str(brightdata_base.get("language", "en")),
            "yandex_region": "84",
            "search_type": "web",
            "use_bing": False,
        }
        provider_arguments["brightdata_bing"] = {
            **brightdata_base,
            "provider_name": "brightdata_bing",
            "country": str(brightdata_base.get("country", "us")),
            "language": str(brightdata_base.get("language", "en")),
            "search_type": "web",
            "use_bing": False,
        }
        provider_arguments["serpapi"] = {
            **provider_arguments.get("serpapi", {}),
            "engine": "baidu",
        }

        plan = SearchPlan.create(
            normalized_query=normalized_query,
            relevance_query=f"{normalized_query}\n{request.research_goal}",
            understanding=understanding,
            options=request.options,
            provider_arguments=provider_arguments,
            branches=branches,
            policy_version=policy.policy_version,
        )
        run.plan = plan
        dc.phase_timings["search.plan"] = (time.monotonic() - started) * 1000.0
        span.set_attribute("search.branch_count", len(branches))
        span.set_attribute("search.intent", dc.intent or "")
        return plan