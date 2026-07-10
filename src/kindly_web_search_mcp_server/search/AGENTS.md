# AGENTS.md - Search Pipeline Core

This directory contains the live search orchestration stack and its provider-facing query controls.

## Current Structure

search/
|-- pipeline.py              # understand -> plan -> execute -> blocklist -> merge -> rerank -> finalize
|-- pipeline_builders.py     # SearchContext, rewrite enrichment, and variant parsing
|-- branch_planner.py        # Explicit target routing for rewrite variants
|-- branch_executor.py       # Branch execution, deadlines, and intent freshness
|-- provider_dispatch.py     # Provider dispatch and selection orchestration
|-- provider_plan.py         # Intent-owned provider bundles and arguments
|-- provider_options.py      # Provider-specific argument construction
|-- provider_call.py         # Single-provider execution wrapper
|-- provider_config.py       # Provider registry and configuration
|-- provider_health.py       # Provider health / cooldown tracking
|-- merge.py                 # Pure rank-based RRF merge
|-- blocklist.py             # DuckDB-backed URL blocklist and compiled matcher
|-- keyword_extract.py       # RAKE-NLTK phrase extraction
|-- literal_passthrough.py  # Expert-syntax detection
|-- query_rewrite_preprocess.py # Rewrite signal bundle and XML prompt block
|-- finalize_results.py      # Final result shaping and public output
|-- intent_policy.py         # Intent routing, freshness, and RRF-k policy
|-- query_rewrite_models.py  # Query rewrite data models
|-- search_router.py         # Router helpers for search variants
|-- options.py               # Search option normalization
|-- context.py               # SearchContext and request context helpers
|-- budget.py                # Branch/result budget helpers
|-- normalize.py             # Query and URL normalization
|-- understanding/resolver.py # Query understanding / intent resolution
|-- flow_observability.py    # Pipeline flow events
|-- merge_observability.py   # Merge-stage events
└-- provider adapters        # Brave, SERP, free-text, neural, and specialized sources

## Query Rewrite and Target Routing

- Non-literal searches produce an original branch plus rewrite variants. The
  `original_free` variant targets `free` providers and preserves the normalized
  user query. `keyword_refined` targets `keyword`/SERP providers, while
  `neural_refined` targets `neural`/semantic providers.
- The target maps are explicit in `branch_planner.py`. Free providers include
  SearXNG, DDG, Gemma, and DeGoog; keyword/SERP routing includes Brave,
  BrightData, Serper, SerpApi, Search Router, SearXNG, DDG, and DeGoog; neural
  routing includes Tavily, Jina, Gemini Search, Grok OpenRouter, Composio,
  Qdrant, and Pollinations. Specialized community providers are selected by
  `IntentSearchPolicy.specialized_providers`, not by these three targets.
- Literal search syntax (quoted phrases, boolean operators, `site:`,
  `filetype:`, `intitle:`, `inbody:`, `ext:`, inclusion, or exclusion operators)
  bypasses the LLM rewrite so the original syntax is preserved. The resulting
  variants still carry the target labels used by the planner.
- Rewrite preprocessing extracts ranked phrases from `research_goal` with
  RAKE-NLTK and merges them into `must_keep_terms`. `keyword_extract.py` imports
  the RAKE runtime inside `_rake_extract()` so standard MCP startup does not load
  NLTK. Brave Autosuggest is requested with `rich=true` only when
  `BRAVE_SUGGEST_API_KEY` is configured; that key is deliberately separate from
  `BRAVE_API_KEY`, and missing suggest credentials skip enrichment rather than
  failing the rewrite. Brave Spellcheck uses the standard `BRAVE_API_KEY` and is
  also best-effort.
- News intent policy sets `freshness="week"`; `branch_executor.py` threads that
  value into keyword-provider arguments while other intents leave freshness unset.

## Brave Retrieval (Phase Two)

- `search/brave.py::search_brave` now calls Brave LLM Context
  (`/res/v1/llm/context`) and parses `grounding.generic` into normalized
  `WebSearchResult` rows. It does not synthesize an answer from Context text.
- `search/brave_news.py` is a specialized provider for the `news` intent. It
  calls `/res/v1/news/search`, maps `page_age` to `published_date`, and is
  scheduled through the `specialized_original` branch rather than paid-SERP
  fan-out.
- Shared Brave request invariants live in `search/brave_common.py` (API key,
  headers, query bounds, freshness translation). `BRAVE_SUGGEST_API_KEY` remains
  suggest-only enrichment; `BRAVE_API_KEY` is required for LLM Context and News.
- Intent Goggles are policy-owned via `BRAVE_GOGGLES_BY_INTENT` (default empty).
  When configured, resolved `goggles` lists are merged into `provider_arguments`
  for `brave` and `brave_news`. The historical yasten Social Goggle URL is an
  operator example only and must not be enabled by default.
- DDGS remains a peer `free` provider (`DDGS.text` only). It is not a fallback
  path and does not accept new freshness/goggle kwargs.

Focused provider tests:

```bash
python -m pytest tests/test_brave_providers.py tests/test_provider_plan.py tests/test_branch_planner.py tests/test_intent_policy.py tests/test_ddg_unit.py tests/test_brightdata_provider.py --basetemp=.pytest-tmp
```

## Merge and Result Hygiene

- Branch result lists are filtered by the DuckDB-backed blocklist immediately
  after branch execution. Patterns are seeded from `uBlacklist.txt`, stored in
  `blocklist_patterns`, compiled into a cached regex, and can be added,
  deactivated, and reloaded at runtime through `blocklist.py`.
- `merge_search_results` performs pure rank-based Reciprocal Rank Fusion:
  each occurrence contributes `1 / (k + rank)`. The intent policy supplies
  `rrf_k` (`news=35`, `digital_humanities=70`, and `60` otherwise). Provider and
  list weights are not part of merge scoring; snippet length is the only
  duplicate-result tie-breaker.
- Provider failures remain observable and non-fatal. Provider option bundles
  may carry call arguments, but no obsolete provider/list weight plumbing may
  be added to branch execution or merge APIs.

## Adding or Changing a Provider

1. Implement a normalized provider module in `search/`.
2. Register it in `search/provider_config.py` and the package wiring when needed.
3. Assign it to an explicit branch target or intent-policy specialization only
   when its query semantics match that target.
4. Add focused tests covering the provider and the orchestrator path that uses it.

## Testing

- `python -m pytest tests/test_search_orchestrator.py tests/test_search_router.py`
- `python -m pytest tests/test_provider_plan.py tests/test_provider_config.py`
- `python -m pytest tests/test_branch_executor.py tests/test_branch_planner.py`

## Conventions

- Return normalized result objects from provider code.
- Keep provider failures observable but non-fatal to the whole request.
- Treat `TOOL_PROFILE` as exposure-only, not as provider-routing logic.
