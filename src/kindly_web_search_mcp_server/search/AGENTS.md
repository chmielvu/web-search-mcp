<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-08-21 | Last verified: 2026-08-21 -->

# AGENTS.md - Search

Shared MCP/CLI web-search pipeline: planning, retrieval, ranking, 24 providers.

## Key Files

| File | Role |
|---|---|
| `service.py` | `execute_web_search()` / `run_search_core()` — sole entry point |
| `contracts.py` | Strict boundary models: `WebSearchRequest`, `SearchRun`, `QueryBranch`, `BranchOutcome` |
| `planning.py` | Normalize, understand intent, 5-variant rewrite, select providers, emit branches |
| `retrieval.py` | Structured branch/provider fanout with budget management |
| `ranking.py` | Blocklist, merge, BM25/rerank, diversity, final response |
| `merge.py` | Canonical deduplication + RRF merge |
| `outcomes.py` | Detached terminal snapshots for async persistence |
| `blocklist.py` | DuckDB-backed URL blocking |
| `provider_catalog.py` | Provider metadata definitions |
| `provider_registry.py` | Adapter lookup for 19 providers |
| `intent_policy.py` | Intent-specific provider subscriptions registry (`get_subscribed_specialized_providers`, `register_provider_subscription`), freshness, options |
| `keyword_extract.py` | Rake/Keybert keyword extraction |
| `providers/` | 27 files — one per provider adapter + base |
| `academic/` | 6 academic adapters (arXiv, Semantic Scholar, OpenAlex, CrossRef, PubMed, CORE) |

## Contracts

`research_goal` is required and nonblank.
`queries` supports up to 4 seed queries for multi-query rewriting; falls back to single `query`.
Query rewrite generates 5 strategic variants: 3 keyword queries, 1 natural-language neural query, and 1 intent-targeted specialized query assigned to `BranchRole.SPECIALIZED`.
`reranking_instructions` passes caller guidance to cross-encoder & LLM rerankers.
- Planning emits at most 10 ordered branches, covering every target from selected providers.
- Provider assignment: only `branch.target in definition.targets`.
- Specialized provider queries are dialect-shaped at the retrieve boundary; `provider_calls` stores both planner `branch_query` and adapter `request_query` plus endpoint/status/result-class diagnostics.
- Blocklist filtering precedes merge, BM25, dense scoring, analytics, and output.
- Pagination is global; providers receive retrieval depth, never result offset.
- `execute_web_search` submits exactly one immutable `SearchOutcome`; background tasks never receive the live `SearchRun`.
- Sourcegraph, GitLab, and GitHub adapters publish structured request metadata through the provider execution context; retrieval persists it without exposing credentials.
- Each `run_provider` invocation starts with fresh request metadata; provider-specific seed fields are initialized inside the request callback so prior-call endpoint/status/error fields cannot leak.

## Bright Data SERP adapter

- Configure `BRIGHTDATA_SERP_ZONE` explicitly; `BRIGHTDATA_ZONE` remains a compatibility alias, while the implicit `sdk_serp` fallback is rejected.
- Google uses `data_format=parsed_light` for web searches requesting at most 10 results, and uses bounded `start` pagination for larger result windows. Bing uses `first`; Yandex uses `p` and never assumes the USA region for non-US countries.
- The parser accepts both the existing URL-based `organic` response and Bright Data's documented Bing `webPages.value` response. HTTP failures retain status, retry, Bright Data error headers, and coarse auth/rate-limit/upstream classification.

## Gemma SERP adapter

- The public provider name remains `gemma`, but its backend is Pollinations `POST /v1/chat/completions` with model `gemini-fast` and `POLLINATIONS_API_KEY`.
- Pollinations `gemini-fast` is backed by Gemini 2.5 Flash-Lite. Pass its documented `{"type": "google_search"}` tool explicitly, keep the system prompt concise and structured with internal query decomposition plus a runtime-date freshness guard, request a strict JSON result object, and record native grounding in diagnostics.
- The provider receives the request's seed `queries` and `research_goal` through provider arguments; the user prompt labels both values as context/data and explains that `queries` guide complementary decomposition while `research_goal` guides relevance ranking.
- A successful HTTP response with blank or unparseable assistant content is an invalid provider response, not a successful empty search; preserve the structured `invalid_response` metadata so retrieval analytics distinguish model-contract failures from valid zero-result responses.


## Query Understanding Gateway

- `search/understanding/resolver.py` makes one combined request through `entity/gliner_client.py`; it does not invoke local ONNX, `gliner2`, or an LLM fallback.
- The hosted contract is `POST /v2/query-understanding`; entity spans must match exact source offsets and relation endpoints require grounded high-confidence entities.
- `search/understanding/adapter.py` is the pure normalization boundary. Keep transport handling in `entity/gliner_client.py` and search policy derivation in the adapter.

## Cold-Start Import Warm-Up

- `keyword_extract.py` keeps `rake_nltk` at module level.
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
```

## Grok Native Search Boundary

- `providers/grok.py` calls xAI's `/v1/responses` endpoint directly and exposes both native `web_search` and `x_search` server-side tools.
- The provider catalog name is `grok_xai`; its reachability credential is `XAI_API_KEY`. Do not route native search through the OpenRouter chat-completions adapter.
- `GROK_BACKEND=vertex` is rejected for this search provider. Vertex's managed Grok Responses endpoint is documented for text Responses, function calling, and structured output, but not xAI's native web/X search tools.
- xAI bills server-side web/X tool invocations separately from model tokens. Preserve `server_side_tool_usage_details` (and the legacy alias), citation count, cache-token, reasoning-token, and total-token fields in telemetry and tool responses.
- xAI web domain filters are nested under the `web_search` tool and allow either `allowed_domains` or `excluded_domains`, not both; X handle/date filters are not part of the current public MCP contract.
