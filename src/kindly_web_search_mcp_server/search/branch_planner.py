"""Build branch specs using explicit query-variant provider targets."""

from __future__ import annotations

from ..settings import settings
from .branch_executor import SearchBranchSpec
from .intents import SearchIntent
from .normalize import normalize_query
from .provider_plan import ProviderExecutionPlan
from .query_rewrite_models import QueryVariant

_FREE_PROVIDER_NAMES = frozenset({"searxng", "ddg", "gemma", "degoog"})
_KEYWORD_PROVIDER_NAMES = frozenset(
    {"brave", "brightdata", "serper", "serpapi", "search_router", "searxng", "ddg", "degoog"}
)
_NEURAL_PROVIDER_NAMES = frozenset(
    {
        "tavily",
        "jina",
        "gemini_search_tool",
        "grok_openrouter",
        "composio_llm_search",
        "qdrant",
        "pollinations",
    }
)
_TARGET_PROVIDERS = {
    "free": _FREE_PROVIDER_NAMES,
    "keyword": _KEYWORD_PROVIDER_NAMES,
    "neural": _NEURAL_PROVIDER_NAMES,
}


def _providers_for_target(target: str, active_provider_names: list[str]) -> list[str]:
    allowed = _TARGET_PROVIDERS.get(target, _KEYWORD_PROVIDER_NAMES)
    return [name for name in active_provider_names if name in allowed]


def build_search_branch_specs(
    *,
    intent: SearchIntent,
    normalized_query: str,
    rewrite_variants: list[QueryVariant],
    num_results: int,
    active_provider_names: list[str],
    provider_plan: ProviderExecutionPlan,
) -> list[SearchBranchSpec]:
    original = QueryVariant(
        kind="original_free",
        target="free",
        query=normalize_query(normalized_query),
        why="Original query is sent to free text-search providers.",
        weight=1.0,
        branch_type="original_free",
        reason="Original query is sent to free text-search providers.",
        max_results=num_results,
    )
    variants = [original, *rewrite_variants[: max(0, settings.query_rewrite_max_variants)]]
    specs: list[SearchBranchSpec] = []
    for index, variant in enumerate(variants):
        providers = _providers_for_target(variant.target, active_provider_names)
        if not providers:
            continue
        specs.append(
            SearchBranchSpec(
                index=index,
                intent=intent,
                query=variant.query,
                branch_type=variant.branch_type or variant.kind,
                weight=variant.weight,
                providers=providers,
                provider_options_by_name=provider_plan.options.bundles,
                max_results=variant.max_results or num_results,
                reason=variant.reason or variant.why,
                must_keep_terms=variant.must_keep_terms,
            )
        )

    # Specialized providers (e.g. telegram, brave_news) fire on the original query.
    specialized = provider_plan.specialized_provider_names
    if specialized:
        specs.append(
            SearchBranchSpec(
                index=len(variants),
                intent=intent,
                query=normalize_query(normalized_query),
                branch_type="specialized_original",
                weight=1.0,
                providers=list(specialized),
                provider_options_by_name=provider_plan.options.bundles,
                max_results=num_results,
                reason="Specialized providers selected by intent policy.",
                must_keep_terms=None,
            )
        )
    if specs:
        return specs
    fallback = _providers_for_target("free", active_provider_names)
    return [
        SearchBranchSpec(
            index=0,
            intent=intent,
            query=normalize_query(normalized_query),
            branch_type="original_free",
            weight=1.0,
            providers=fallback or active_provider_names,
            provider_options_by_name=provider_plan.options.bundles,
            max_results=num_results,
            reason="Fallback original query branch.",
        )
    ]
