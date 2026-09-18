<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-09-17 | Last verified: 2026-09-17 -->

# AGENTS.md - Search

Shared MCP/CLI web-search pipeline: planning, retrieval, ranking, 15 providers.

## Key Files

| File | Role |
|---|---|
| `service.py` | `execute_web_search()` / `run_search_core()` — sole entry point |
| `contracts.py` | Strict boundary models: `WebSearchRequest`, `SearchRun`, `QueryBranch`, `BranchOutcome` |
| `types.py` | Frozen internal provider/run dataclasses: `SearchHit`, `EngineCall`, `ScoredHit`, `SearchRunResult` |
| `evidence.py` | Shared testimony rendering/serialization for ranking, rerank, indexing, and analytics |
| `planning.py` | Normalize, understand intent, 5-variant rewrite, select providers, emit branches |
| `graph_expansion.py` | Rewrite-only, bounded related-query injection from a SQLite graph artifact; preserves the normalized original seed and six-branch topology |
| `retrieval.py` | Structured branch/provider fanout with budget management |
| `ranking.py` | Blocklist, weighted RRF merge, BM25/rerank, final response |
| `merge.py` | Canonical dedup + weighted RRF (`w/(k+rank)`) |
| `outcomes.py` | Detached terminal snapshots for async persistence |
| `blocklist.py` | SQLite-backed URL blocking |
| `provider_registry.py` | Provider definitions (15), adapter wiring, reachability, round-robin selection, diagnostics (merged `provider_catalog.py`) |
| `intents.py` | Canonical intents, aliases, normalization + intent-specific provider arguments, goggles, freshness, options (merged `intent_policy.py`) |
| `keyword_extract.py` | YAKE support-term extraction (async-off-loop) |
| `providers/` | 20 files — one per provider adapter + base |
| `academic/` | 6 academic adapters (arXiv, Semantic Scholar, OpenAlex, CrossRef, PubMed, CORE) + `citation_graph.py` |
| `quick/` | Quick web search modes: Parallel web, YouTube discovery, and library documentation |
| `filters.py` | Temporal/locale normalization (`TemporalWindow`, `LocaleSpec`, wire-token mappers) |
- Bright Data Google supports web/news freshness and mobile SERP emulation through provider kwargs. Full JSON harvesting keeps only URL-bearing, citable rows and orders mixed SERP sections by native `global_rank` before applying the requested result bound.
- Bright Data query-ban and verification errors carry a 15-second retry hint but are not retried inside the interactive request budget.

## Contracts

`research_goal` is required and nonblank.
`queries` supports up to 4 seed queries for multi-query rewriting; falls back to single `query`.
Query rewrite generates 5 variants: one free query, two SERP queries, one semantic Tavily query, and one semantic Exa query. The six ordered `BranchRole` values are `original`, `free`, `serp1`, `serp2`, `semantic_tavily`, and `semantic_exa`.
`reranking_instructions` passes caller guidance to cross-encoder & LLM rerankers.
- Planning emits exactly 6 ordered branches with the provider assignments defined in `search/planning.py`.
- Provider assignment: only `branch.provider_names` are dispatched.
- Specialized provider queries are dialect-shaped at the retrieve boundary; `provider_calls` stores both planner `branch_query` and adapter `request_query` plus endpoint/status/result-class diagnostics.
- Blocklist filtering precedes merge, BM25, dense scoring, analytics, and output.
- Graph expansion is consumed only when rewrite is enabled and its feature flag is on. Effective seeds are stable-deduplicated, start with the normalized original query, remain capped at four, and carry SQLite generation/fingerprint/age/support/drop-reason metadata. It never adds provider branches or graph ranking features.
- Pagination is global; providers receive retrieval depth, never result offset.
- `execute_web_search` submits exactly one immutable `SearchOutcome`; background tasks never receive the live `SearchRun`.
- Specialized adapters (Telegram, Hacker News, Reddit, Brave News) publish structured request metadata through the provider execution context; retrieval persists it without exposing credentials. Public-code providers were removed from web_search.
- Merge uses weighted RRF. Same-provider lists from multiple branches collapse to one list (best rank kept) before fusion. Weights: `settings.rrf_provider_weights` + `rrf_bm25_weight`. `provider_consensus_rrf_score` is deleted.
- Each `run_provider` invocation starts with fresh request metadata; provider-specific seed fields are initialized inside the request callback so prior-call endpoint/status/error fields cannot leak. Adapters return a typed `EngineCall` envelope carrying `hits: tuple[SearchHit, ...]`, `expansion: tuple[str, ...]`, `integrity: QueryIntegrity | None`, and `failure: EngineFailure | None`.
- Bright Data Google SERP requests use Full JSON exclusively (`data_format=parsed_light` is deleted) and send `"data_options": {"return_mismatch": true}` in the API request body per primary documentation, allowing query truncation and cloaking to arrive as data for pipeline validation.
- `rank_and_finalize` wraps RRF and reranked hits into `ScoredHit` items, stamping agent evidence via `attach_agent_evidence`: `citation_id` ("c1", "c2"...), `evidence_final`, `evidence_semantic`, `evidence_lexical`, `evidence_consensus`, `freshness_signal` (`fresh`, `dated`, `unknown`), and `fetch_hint_query` (target URL for the fetch tool). Multi-engine consensus is strictly isolated to `evidence_consensus`. The run result is emitted as a frozen `SearchRunResult` whose `hits` field is a tuple.
- The public MCP/CLI envelope is `WebSearchPublicResponse`: ranked `results`, optional `warnings`, `next` (fetch of at most 5 URLs), and `cursor`/`remaining` for leftover title/url links. It does not ship `status`, planner `intent`/`query_variants`, hit `score`, leftover `stage`, or `next.action`. Snippets are teasers; fetch reads pages.
- Provider-native evidence remains internal: `SearchHit` retains native rank, provider score, source name/kind, publication date, highlights, answer kind, SearXNG contributing engines, and recalled origin adapters. Adapters must not fabricate a URL for URL-less answer text. Merge unions complementary testimony onto the longest-snippet variant. Ranking/indexing consume unique highlight passages; BM25 may append at most two expansion seeds independently supported by two provider calls. Query truncation lands as a typed retrieval warning and a `partial` provider-call status.

## Temporal & locale filters (`filters.py`)

Public contract: `date_range` (relative), `after_date`/`before_date` (absolute; wins over relative
with a parameter warning), `language` (ISO 639-1 / BCP-47), `region` (alpha-2). The MCP `gl` parameter is removed; `gl`/`country` still rewrite to `region` in alias middleware. Resolution happens once via `resolve_window`/`normalize_locale`; values ride on
`SearchOptions.temporal|language|region` and are part of the cache fingerprint. Adapter mappings are
verified against primary docs (Brave freshness incl. custom ranges; Tavily absolute dates + country
names for topic=general; Serper qdr buckets + gl/hl; Exa ISO published dates + userLocation;
ddgs timelimit/region; LangSearch one* buckets; SearXNG day/month/year only — `week` degrades).
Absolute windows additionally get a lenient post-filter; undated results follow
`should_drop_undated`: default `capability_default` drops them only from providers without native
date support, with `--include-undated`/`--exclude-undated` overrides. Counters land on
`SearchRunResult.filter_stats` and degradation surfaces as `provider="filters"` warnings.

## Academic citation graph (`academic/citation_graph.py`)
`cited_by_paper_id` / `references_paper_id` / `author_id` route to Semantic Scholar Graph API and
OpenAlex only. References are classified by `classify_paper_ref` (DOI/arXiv/PMID/OpenAlex/S2/ORCID);
S2 paper refs use prefixed forms (`DOI:…`, `ARXIV:…`). OpenAlex outgoing references hydrate the
work's `referenced_works`. The orchestrator restricts filtered runs to these two providers and emits
a structured warning listing skipped sources. Lookups fail open (empty list + log) like siblings.

## Bright Data SERP adapter

Bright Data exposes only the Google SERP provider under the catalog name `brightdata`.

- One module: `providers/brightdata.py` holds the Google entry point, the Google target-URL builder, the response parser, upstream-error detection, and the bounded pagination transport for `POST https://api.brightdata.com/request`.
- Configure `BRIGHTDATA_SERP_ZONE` explicitly; `BRIGHTDATA_ZONE` remains a compatibility alias, while the implicit `sdk_serp` fallback is rejected. An account whose real SERP zone is named `sdk_serp` must therefore set the variable explicitly — the settings default is not enough.
- Google uses Full JSON for every search and bounded `start` pagination for larger result windows. Mixed answer, news, and organic rows are ordered by documented `global_rank`, with unranked evidence following ranked rows.
- Query-integrity checks compare `general.query` (the effective engine query) with `general.detected_query`, using `spelling` to distinguish correction from truncation. HTTP/envelope failures retain status, retry, both Bright Data error-header families, and coarse auth/rate-limit/upstream classification.
- Destination-link fidelity: Google may return Google redirect links (`https://www.google.com/goto?url=…`). The displayed host is preferred when available so redirect URLs do not erase the source domain.

## Gemma SERP adapter

- The public provider name remains `gemma`, but its backend is Pollinations `POST /v1/chat/completions` with model `gemini-fast` and `POLLINATIONS_API_KEY`.
- Pollinations `gemini-fast` is backed by Gemini 2.5 Flash-Lite. Pass its documented `{"type": "google_search"}` tool explicitly, keep the system prompt concise and structured with internal query decomposition plus a runtime-date freshness guard, request a strict JSON result object, and record native grounding in diagnostics.
- The provider receives the request's seed `queries` and `research_goal` through provider arguments; the user prompt labels both values as context/data and explains that `queries` guide complementary decomposition while `research_goal` guides relevance ranking.
- A successful HTTP response with blank or unparseable assistant content is an invalid provider response, not a successful empty search; preserve the structured `invalid_response` metadata so retrieval analytics distinguish model-contract failures from valid zero-result responses.

## Google Discovery Engine adapter

- Catalog name `google_discovery_engine`. ``APPS`` maps intents to serving configs. Builtin `search-1` covers `general` and `ai_coding_and_infrastructure` on the original and free branches. A future news engine is another ``APPS`` row, not a new adapter.
- Auth is OAuth bearer. ADC only when a credentials file exists, else
  ``gcloud auth print-access-token`` (stdin=DEVNULL). Token is warmed in
  ``plan_search`` so mint sits outside the 15s retrieve cap; cache 55
  minutes; 401 retries once. Every call sends ``x-goog-user-project``.
  `:search` rejects API keys.
- Reachability is gcloud-on-disk or `GOOGLE_APPLICATION_CREDENTIALS`. Disable with `DISABLED_PROVIDERS`.

## Query Understanding Gateway

- Query understanding calls deployed unified-ml `POST /classify` + `POST /ner`. There is no `/v2/query-understanding` on that container. Entity spans must match exact source offsets. `_rewrite_queries` unions `preserved_terms` with grounded entity surfaces into Preserve Exactly.
- `search/understanding/adapter.py` is the pure normalization boundary. Keep transport handling in `ml/gliner_client.py` and search policy derivation in the adapter.

## Cold-Start Import Warm-Up

- `keyword_extract.py` imports `yake` at module level (pure Python, no import-lock risk).
- `llm/router.py` pre-imports `openai.resources.chat`.
- `server.py:_warm_heavy_imports()` is called from `main()` before `mcp.run()`.
- Reason: Prevents Python global import lock from blocking event loop during first stdio tool call.

## LLM Run Attribution

`tools/search.py::web_search` binds `_run_key_ctx` + `_operation_ctx` via
`bind_run_context(tool_call_id, operation="web_search")` before calling
`execute_web_search`. The `finally` block calls `reset_run_context`.
Downstream planner, rewrite, query understanding, and judge calls inherit
attribution through ContextVar lookups in `LLMRouter._complete`.

## Testing

```bash
uv run pytest tests/test_provider_registry.py tests/test_bm25_rerank.py tests/test_search_service.py
uv run pytest tests/test_search_orchestrator.py tests/test_search_contracts.py
uv run pytest tests/test_search_ranking.py tests/test_search_planning_why.py
uv run pytest tests/test_search_provider_data.py tests/test_search_index_roundtrip.py
```

## Grok Native Search Boundary

- `providers/grok.py` calls xAI's `/v1/responses` endpoint directly and exposes both native `web_search` and `x_search` server-side tools.
- The provider catalog name is `grok_xai`; its reachability credential is `XAI_API_KEY`. Do not route native search through the OpenRouter chat-completions adapter.
- `GROK_BACKEND=vertex` is rejected for this search provider. Vertex's managed Grok Responses endpoint is documented for text Responses, function calling, and structured output, but not xAI's native web/X search tools.
- xAI bills server-side web/X tool invocations separately from model tokens. Preserve `server_side_tool_usage_details` (and the legacy alias), citation count, cache-token, reasoning-token, and total-token fields in telemetry and tool responses.
- xAI web domain filters are nested under the `web_search` tool and allow either `allowed_domains` or `excluded_domains`, not both; X handle/date filters are not part of the current public MCP contract.
