# AGENTS.md - Search Pipeline Core

This directory contains the live search orchestration stack.

## Current Structure

search/
|-- pipeline.py              # Top-level orchestration: understand -> plan -> fanout -> merge -> rerank -> finalize
|-- branch_planner.py        # Query branching and rewrite variant planning
|-- branch_executor.py       # Branch execution and deadline handling
|-- provider_dispatch.py     # Provider dispatch and selection orchestration
|-- provider_plan.py         # Intent-owned provider bundles and weights
|-- provider_options.py      # Provider-specific argument construction
|-- provider_call.py         # Single-provider execution wrapper
|-- provider_execution.py    # Shared execution helpers for providers
|-- provider_config.py       # Provider registry and configuration
|-- provider_health.py       # Provider health / cooldown tracking
|-- merge.py                 # RRF merge across providers
|-- finalize_results.py      # Final result shaping and public output
|-- intent_policy.py         # Intent-owned provider weights and overrides
|-- query_policy.py          # Lightweight rewrite policy model
|-- query_rewrite_models.py  # Query rewrite data models
|-- search_router.py         # Router helpers for search variants
|-- options.py               # Search option normalization
|-- context.py               # SearchContext and request context helpers
|-- budget.py                # Branch/result budget helpers
|-- normalize.py             # Query and URL normalization
|-- understanding/resolver.py # Query understanding / intent resolution
|-- flow_observability.py    # Pipeline flow events
|-- merge_observability.py    # Merge-stage events
|-- academic_*.py            # Academic source adapters
|-- brave.py, ddg.py, searxng.py, tavily.py, jina.py, brightdata.py, serper.py, serpapi.py
|-- google_cse.py, github_graphql.py, hackernews.py, reddit.py, grok.py
|-- gemini_search_tool.py, pollinations.py, composio_llm_search.py, qdrant.py

## Current Behavior

- `IntentSearchPolicy` owns provider selection; backend `SearchProfile`
  routing is no longer part of the live tree.
- Branch planning/execution is the unit that fans queries out across provider
  bundles and cooperatively handles deadlines.
- `merge.py` still performs RRF merge, and reranking happens after merge in
  the main pipeline.
- Provider health and cooldown state are tracked separately from provider
  registration.

## Adding or Changing a Provider

1. Implement a normalized provider module in `search/`
2. Register it in `search/__init__.py` and `search/provider_config.py`
3. Add intent or plan changes in `intent_policy.py` / `provider_plan.py` if
   the provider needs different weights or args
4. Add tests that cover the provider and the orchestrator path that uses it

## Testing

- `python -m pytest tests/test_search_orchestrator.py tests/test_search_router.py`
- `python -m pytest tests/test_provider_plan.py tests/test_provider_config.py`
- `python -m pytest tests/test_branch_executor.py tests/test_branch_planner.py`

## Conventions

- Return normalized result objects from provider code
- Keep provider failures observable but non-fatal to the whole request
- Treat `TOOL_PROFILE` as exposure-only, not as provider-routing logic
