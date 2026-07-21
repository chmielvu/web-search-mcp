"""Deterministic six-branch planning for the shared web-search service."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Sequence

from ..llm.router import build_worker_router
from ..telemetry.spans import get_tracer
from .providers.brave import spellcheck_brave, suggest_brave_queries
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
_NEURAL_CANDIDATES = ("gemma", "qdrant", "composio_llm_search", "langsearch")
_PAID_GOOGLE_CANDIDATES = ("brightdata", "serper", "search_router")
_PAID_OTHER_CANDIDATES = ("brightdata_yandex", "brightdata_bing", "serpapi")


class _RewriteQueries(ContractModel):
    queries: list[str]


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


_REWRITE_SYSTEM = (
    "Rewrite the user query into effective search queries: three keyword "
    "queries for Brave/Google/Bing/Yandex using search operators, and one "
    "natural-language neural query for Exa-style semantic search."
)

_REWRITE_USER = """You are a search query optimizer that generates strategic search queries for web search engines.

TASK: Given a user query, a research goal, and enrichment evidence, generate 3 keyword search variants (for Brave/Google/Bing/Yandex) plus 1 natural-language neural query (for Exa-style semantic search) that explore complementary aspects.

<CURRENT_CONTEXT>
Current Year: {current_year}
Query: "{query}"
Research Goal: "{research_goal}"
</CURRENT_CONTEXT>

<ENRICHMENT_EVIDENCE>
Support Terms: {support_terms}
Autosuggest Suggestions: {suggestions}
Spell Correction: {spell_correction}
</ENRICHMENT_EVIDENCE>

<QUERY_NORMALIZATION>
Transform the input into effective search queries by:
- Converting questions to search terms (e.g., "What is X?" → "X explanation guide")
- Organizing keyword dumps into coherent searches
- Removing unnecessary words (how, what, when, etc.) unless essential
- Preserving technical terms, specific models, brands or products, and quoted phrases exactly
- If spell_correction is non-empty, prefer it over the raw query
</QUERY_NORMALIZATION>

<KEYWORD_QUERY_RULES>
The three keyword queries target Brave/Google/Bing/Yandex. Use search operators to make each query target a DIFFERENT facet:
- "exact phrase"  → force an exact multi-word match
- site:domain     → restrict to a specific site/domain
- intitle: / inbody: / inpage:  → require term in title/body/either
- filetype: / ext:  → restrict to a file type
- lang: / loc:     → restrict to language / country (ISO codes)
- +term           → force inclusion of a term
- -term           → exclude a term
- AND / OR / NOT  → combine conditions (uppercase)
Make the three queries genuinely different in structure and operator use
(e.g., one exact-phrase + site-scoped, one with -exclusions, one broad with +required terms).
Preserve any operators or mandatory terms already present in the query.
</KEYWORD_QUERY_RULES>

<NEURAL_QUERY_RULES>
The neural query targets Exa-style semantic/vector search. Write it as a single
full descriptive SENTENCE with NO operators — the engine retrieves by meaning,
not keyword matching. Include the core intent and key entities from the query
and research goal. Do NOT use quotes, site:, -, +, or any operator syntax.
</NEURAL_QUERY_RULES>

<INSTRUCTIONS>
1. First, normalize the query for search (apply spell_correction if present):
   - If it's a natural language question, extract key search terms
   - If it's a keyword dump, organize into a coherent phrase
   - Keep quoted phrases, technical terms and specific brands intact
2. Generate three DIFFERENT keyword queries using the operator rules above
3. Generate one natural-language neural query using the neural rules above
4. Add temporal markers ({current_year}) to keyword queries where appropriate

IMPORTANT: Always generate EXACTLY 3 keyword queries + 1 neural query - no more, no less.
</INSTRUCTIONS>

<TEMPORAL_RULES>
- Technical queries: Add {current_year} for the current year
- Historical queries: Preserve specific years
- Quoted phrases: Keep exactly as-is
</TEMPORAL_RULES>

<EXAMPLES>
Example 1 - Docker container orchestration:
Query: "Docker container orchestration"
Research Goal: "Find production-grade orchestration tooling"
Output queries: [
  "Docker container orchestration {current_year}",
  "container orchestration best practices site:docs.docker.com",
  "Docker orchestration Kubernetes -swarm +production",
  "What are the best production-grade tools for orchestrating Docker containers in {current_year}?"
]

Example 2 - Spelling corrected:
Query: "pytorch attension mechanism"
Spell Correction: "pytorch attention mechanism"
Output queries: [
  "pytorch attention mechanism tutorial",
  "attention mechanism implementation site:pytorch.org",
  "pytorch transformer attention -nlp +code",
  "How do I implement the attention mechanism in PyTorch with a working code example?"
]
</EXAMPLES>

Return JSON of the form:
{{"queries": ["<keyword1>", "<keyword2>", "<keyword3>", "<neural>"]}}"""


async def _rewrite_queries(
    *,
    query: str,
    research_goal: str,
    terms: tuple[str, ...],
    suggestions: tuple[str, ...],
    correction: str | None,
    current_year: str,
) -> tuple[_RewriteQueries, dict[str, Any]]:
    user_content = _REWRITE_USER.format(
        current_year=current_year,
        query=query,
        research_goal=research_goal,
        support_terms=list(terms),
        suggestions=list(suggestions),
        spell_correction=correction or "",
    )
    started = time.monotonic()
    generation = await build_worker_router().complete_json(
        messages=[
            {"role": "system", "content": _REWRITE_SYSTEM},
            {"role": "user", "content": user_content},
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

        fallback = (
            brave_fallback,
            keyword_query,
            keyword_query,
            normalized_query,
            normalized_query,
        )

        # --- resolve query texts ---
        if request.rewrite:
            try:
                rewrite, rewrite_meta = await _rewrite_queries(
                    query=normalized_query,
                    research_goal=request.research_goal,
                    terms=terms,
                    suggestions=suggestions,
                    correction=correction,
                    current_year=time.strftime("%Y"),
                )
                rewrite_meta["branch_count"] = 6
                dc.rewrite_metadata = rewrite_meta
                if len(rewrite.queries) < 4:
                    raise ValueError(
                        f"Expected 4 queries, got {len(rewrite.queries)}: {rewrite.queries}"
                    )
                q0, q1, q2, q3 = (normalize_query(q) for q in rewrite.queries[:4])
                queries = (
                    q0,
                    q1,
                    q2,
                    q3,
                    q3,  # SPECIALIZED reuses neural query
                )
            except Exception as exc:
                LOGGER.warning(
                    "Query rewrite failed; using deterministic fallback: %s", type(exc).__name__
                )
                dc.rewrite_metadata = {"error": type(exc).__name__, "branch_count": 6}
                queries = fallback
        else:
            dc.rewrite_metadata = {"branch_count": 6}
            queries = fallback

        # Persist the 4 planner rewrites (k1, k2, k3, neural) separately from
        # the 6-branch dispatched topology. Empty tuple when rewrite was
        # disabled or errored — the judge then writes no rewrite rows.
        rewrite_queries: tuple[str, ...] = (
            tuple(rewrite.queries[:4])
            if request.rewrite and dc.rewrite_metadata and "error" not in dc.rewrite_metadata
            else ()
        )

        # `use_llm_why` is true when the LLM-rewrite path succeeded; in
        # every other case (rewrite disabled, rewrite errored, no
        # metadata) the branches use their deterministic fallback.
        use_llm_why = bool(
            request.rewrite and dc.rewrite_metadata and "error" not in dc.rewrite_metadata
        )
        # Display name for the deterministic branch `why` string. Keys
        # must match the BranchRole values used below.
        _DETERMINISTIC_WHY = {
            BranchRole.PAID_BRAVE: "deterministic Brave query",
            BranchRole.PAID_GOOGLE: "deterministic Google query",
            BranchRole.PAID_OTHER: "deterministic paid-other query",
            BranchRole.NEURAL: "deterministic neural query",
            BranchRole.SPECIALIZED: "deterministic specialized query",
        }

        def _why_for(role: BranchRole, llm_label: str) -> str:
            return llm_label if use_llm_why else _DETERMINISTIC_WHY[role]

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
                why=_why_for(BranchRole.PAID_BRAVE, "LLM paid_brave"),
                support_terms=terms,
                max_results=request.num_results,
            ),
            QueryBranch(
                role=BranchRole.PAID_GOOGLE,
                query=queries[1],
                provider_names=paid_google,
                why=_why_for(BranchRole.PAID_GOOGLE, "LLM paid_google"),
                support_terms=terms,
                max_results=request.num_results,
            ),
            QueryBranch(
                role=BranchRole.PAID_OTHER,
                query=queries[2],
                provider_names=paid_other,
                why=_why_for(BranchRole.PAID_OTHER, "LLM paid_other"),
                support_terms=terms,
                max_results=request.num_results,
            ),
            QueryBranch(
                role=BranchRole.NEURAL,
                query=queries[3],
                provider_names=neural,
                why=_why_for(BranchRole.NEURAL, "LLM neural"),
                support_terms=terms,
                max_results=request.num_results,
            ),
            QueryBranch(
                role=BranchRole.SPECIALIZED,
                query=queries[4],
                provider_names=specialized,
                why=_why_for(BranchRole.SPECIALIZED, "LLM specialized"),
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
        }
        provider_arguments["brightdata_bing"] = {
            **brightdata_base,
            "provider_name": "brightdata_bing",
            "country": str(brightdata_base.get("country", "us")),
            "language": str(brightdata_base.get("language", "en")),
            "search_type": "web",
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
            rewrite_queries=rewrite_queries,
        )
        run.plan = plan
        dc.phase_timings["search.plan"] = (time.monotonic() - started) * 1000.0
        span.set_attribute("search.branch_count", len(branches))
        span.set_attribute("search.intent", dc.intent or "")
        return plan
