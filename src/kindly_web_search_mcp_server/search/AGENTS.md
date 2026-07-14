# AGENTS.md - Search Application Service

This directory owns the shared MCP/CLI web-search pipeline.

## Live Path

- `service.py` — `execute_web_search` and `run_search_core`; sole terminal owner.
- `contracts.py` — strict boundary models plus immutable plan/outcome records.
- `planning.py` — normalize, understand, enrich, rewrite, provider selection, target coverage.
- `provider_registry.py` — immutable 19-provider definitions and adapters.
- `retrieval.py` — structured branch/provider fanout with one phase-level retrieve budget.
- `ranking.py` — blocklist, merge, BM25/rerank, global pagination, response.
- `outcomes.py` — detached terminal snapshots and bounded shutdown drain.
- `merge.py` — canonical deduplication and provider RRF.
- `blocklist.py` — DuckDB-backed URL blocking; apply before every scoring stage.
- `intent_policy.py` — intent-specialized providers, arguments, freshness, and RRF-k.

MCP `tools/search.py` and CLI `cli/services/search_web.py` must both construct
`WebSearchRequest` and call `execute_web_search`. Do not add orchestration to
either adapter.

## Contracts

- `research_goal` is required and nonblank.
- `num_results` is strictly 15–50; do not clamp invalid values.
- `rewrite=False` skips only the rewrite LLM. Keyword extraction, Brave
  Autosuggest, and Brave Spellcheck still run with independent 10-second bounds.
- Planning emits at most 10 ordered branches and covers every target represented
  by selected providers.
- Provider assignment is only `branch.target in definition.targets`.
- Blocklist filtering precedes merge, BM25, dense scoring, analytics, and output.
- `rank_and_finalize` projects every non-None `RerankEmbeddingContext` into the diagnostics collector as both `candidate_embeddings` AND `query_embedding`; analytics `query_embeddings` persistence must remain non-zero on any rerank path that returned a context.
- Pagination is global; providers receive retrieval depth, never result offset.
- `execute_web_search` submits exactly one immutable success/error/cancelled
  `SearchOutcome`; background tasks never receive the live `SearchRun`.

## Providers

Registry selection is ordered: all reachable free providers, reachable Bright
Data plus one other paid SERP provider by locked round-robin, then only
intent-selected reachable specialized providers. Credentials and disabled
providers are checked during planning; every planned provider is attempted.

Provider-specific option translation belongs in the provider adapter. Never
reintroduce signature inspection, hard-coded branch provider sets, or silent
option emulation.

## Testing

```powershell
python -m pytest --basetemp=.pytest-tmp tests/test_provider_registry.py tests/test_bm25_rerank.py tests/test_search_service.py
```

Keep provider errors attributed and nonfatal when another provider returns
usable rows. Caller cancellation must cancel and await every child task before
being re-raised.
