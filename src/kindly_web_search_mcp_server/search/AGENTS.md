# AGENTS.md - Search

Shared MCP/CLI web-search pipeline: planning, retrieval, ranking, 19 providers.

## Key Files

| File | Role |
|---|---|
| `service.py` | `execute_web_search()` / `run_search_core()` — sole entry point |
| `contracts.py` | Strict boundary models: `WebSearchRequest`, `SearchRun`, `QueryBranch`, `BranchOutcome` |
| `planning.py` | Normalize, understand intent, rewrite, select providers, emit branches |
| `retrieval.py` | Structured branch/provider fanout with budget management |
| `ranking.py` | Blocklist, merge, BM25/rerank, diversity, final response |
| `merge.py` | Canonical deduplication + RRF merge |
| `outcomes.py` | Detached terminal snapshots for async persistence |
| `blocklist.py` | DuckDB-backed URL blocking |
| `provider_catalog.py` | Provider metadata definitions |
| `provider_registry.py` | Adapter lookup for 19 providers |
| `intent_policy.py` | Intent-specific providers, freshness, options, RRF-k |
| `keyword_extract.py` | Rake/Keybert keyword extraction |
| `providers/` | 27 files — one per provider adapter + base |
| `academic/` | 6 academic adapters (arXiv, Semantic Scholar, OpenAlex, CrossRef, PubMed, CORE) |

## Contracts

- `research_goal` is required and nonblank.
- `num_results` is strictly 15–50; do not clamp invalid values.
- Planning emits at most 10 ordered branches, covering every target from selected providers.
- Provider assignment: only `branch.target in definition.targets`.
- Blocklist filtering precedes merge, BM25, dense scoring, analytics, and output.
- Pagination is global; providers receive retrieval depth, never result offset.
- `execute_web_search` submits exactly one immutable `SearchOutcome`; background tasks never receive the live `SearchRun`.

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
