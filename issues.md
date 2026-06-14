# Search Latency And Quality Issues

Scope:
- `duckdb_data/analytics/search_events.duckdb`
- `duckdb_data/logs/process_logs.duckdb`
- `src/kindly_web_search_mcp_server/search/`
- `src/kindly_web_search_mcp_server/analytics/`

## Executive Summary

The latency problem is systemic, not a single bad provider call. The common path combines:
- 3 rewrite branches on most non-trivial queries.
- A wide provider plan on every branch.
- A paid SERP semaphore that caps concurrency, but does not reduce provider count.
- Several low-yield or failing providers that add time without adding results.
- A rerank stage that adds a consistent post-merge tail.

The search-quality layer is also incomplete in the live analytics sink:
- `compute_search_quality()` is invoked, but it fails because `rerank_candidates` is missing from the live DuckDB file.
- The current analytics database does not contain `search_quality_scores` or `judge_evaluations`.
- Several declared payload fields are always NULL, so quality attribution is weakened even when the pipeline succeeds.

## Latency Snapshot

Last 2 days, `search_runs`:

| n | avg | p50 | p95 | max |
|---|---:|---:|---:|---:|
| 45 | 100.0s | 88.4s | 212.7s | 291.2s |

Last 2 days, selected events:

| event | n | avg | p95 | max |
|---|---:|---:|---:|---:|
| `provider.search.error` | 169 | 9.3s | 28.7s | 48.6s |
| `provider.search.result` | 982 | 7.8s | 25.5s | 52.5s |
| `search.cache.lookup` | 97 | 0.0s | 0.1s | 0.1s |
| `search.merge.summary` | 203 | 0.0s | 0.0s | 0.0s |
| `search.rerank.summary` | 32 | 13.5s | 41.6s | 45.9s |

Interpretation:
- Merge is effectively free.
- Cache lookup is effectively free.
- Rerank is a real tail, with a sustained 8-46s tax.
- Provider calls dominate the wall clock.

## Branch Fan-Out

The branch event payload shows `branch_count = 3` on the hot path. Example payloads include:
- original query branch
- keyword rewrite branch
- community/long-form branch

Observed branch latency from one representative run:
- branch 0: ~9.2s
- branch 1: ~30.2s
- branch 2: ~32.7s

This matters because the tail is paid per branch, not just per provider.

Live summary:
- `search.pipeline.branches` events: 52
- average branch count: 2.9
- max branch count: 3

## Provider Findings

Last 2 days, provider result latency:

| provider | calls | avg | p95 | max | zero-result % |
|---|---:|---:|---:|---:|---:|
| `brightdata` | 16 | 27.9s | 44.0s | 52.5s | 100.0% |
| `serpapi` | 16 | 18.6s | 36.5s | 40.7s | 12.5% |
| `searxng` | 153 | 12.6s | 35.4s | 43.5s | 3.9% |
| `github_graphql` | 153 | 8.4s | 22.1s | 36.3s | 47.7% |
| `search_router` | 152 | 7.3s | 20.7s | 43.0s | 2.6% |
| `brave` | 153 | 6.0s | 17.8s | 28.1s | 2.6% |
| `ddg` | 153 | 4.9s | 13.1s | 20.3s | 0.0% |
| `hackernews` | 153 | 4.9s | 14.0s | 50.4s | 98.7% |
| `serper` | 16 | 4.6s | 11.2s | 15.0s | 12.5% |
| `composio_llm_search` | 16 | 7.7s | 15.5s | 17.2s | 0.0% |

Main takeaways:
- `brightdata` is the single worst latency offender and returns no useful rows in the sampled period.
- `hackernews` fires constantly and is nearly always empty.
- `github_graphql` is expensive enough to matter and is empty almost half the time.
- `reddit` and `gemini` are not in the table above because they mostly show up as failures, not results.

## Error Tax

Last 2 days, provider error logs:

| provider | errors | avg fail time |
|---|---:|---:|
| `reddit` | 98 | 12.7s |
| `gemini` | 70 | 4.8s |
| `search_router` | 1 | 1.8s |

Log evidence:
- `reddit` consistently returns HTTP 403 Blocked.
- `gemini` consistently hits rate limits.
- Circuit breaker logs show repeated open / half-open thrash for both providers.

Process-log buckets over the last 7 days:
- `reddit_blocked`: 288
- `gemini_rate_limited`: 204
- `breaker_open`: 176
- `breaker_half_open`: 31
- `otlp_export`: 115
- `quality_compute_failed`: 42

## Search Quality Snapshot

Search-run shape over the last 7 days:

| profile | runs | avg candidate count | avg final results | rewrite-on % |
|---|---:|---:|---:|---:|
| `general` | 23 | 13.2 | 10.0 | 91.3% |
| `ai_coding` | 12 | 11.1 | 10.0 | 83.3% |
| `comparison` | 10 | 11.0 | 10.0 | 90.0% |

Other run-shape metrics:
- average candidate count overall: 12.1
- average final results overall: 10.0
- `has_more` is true on 22.2% of runs
- `result_offset` is always 0 in the sampled window

Interpretation:
- The pipeline is usually saturating the requested result count.
- Quality is therefore about ranking and source mix, not just raw result volume.

## Analytics Sink Problems

The live analytics database currently contains only:
- `final_results`
- `merged_candidates`
- `query_rewrites`
- `query_understanding`
- `rerank_stages`
- `search_events`
- `search_runs`

It does **not** contain:
- `rerank_candidates`
- `search_quality_scores`
- `judge_evaluations`

That lines up with the logs:
- `compute_search_quality failed: Catalog Error: Table with name rerank_candidates does not exist`
- 42 such failures in the last 7 days

Code path:
- `src/kindly_web_search_mcp_server/search/pipeline.py` calls `compute_search_quality(run_key)` in a best-effort `try/except`.
- The exception is logged only at `DEBUG`, so the analytics failure is silent in normal operation.

Impact:
- Daily quality summary views cannot be trusted.
- Search quality is effectively blind in the live sink.
- The pipeline keeps collecting latency data, but quality attribution stalls.

## Observability Gaps

Additional issues surfaced in the logs:
- OTLP trace export is broken. The exporter receives an HTML 404 page instead of an OTLP response.
- `search.cache.lookup` currently records `cache_hit` as NULL for every row in the sampled window.
- `search.rerank.summary` currently leaves `engine`, `candidate_count`, and `kept` NULL in the analytics payloads.

These are not the root cause of latency, but they reduce the usefulness of the telemetry.

## Bright Data And GitHub MCP Notes

Bright Data:
- The official Bright Data MCP repo documents the remote endpoint as `https://mcp.brightdata.com/mcp?token=...`.
- It also documents `search_engine` and the `engine` parameter with `google`, `bing`, and `yandex`.
- So the local Bright Data call shape is not obviously wrong at the MCP-method level.
- The problem is orchestration: the provider is called too broadly, on too many branches, for too little yield.

GitHub:
- The official GitHub MCP server is a separate MCP server for repos, issues, and pull requests.
- The local `search/github_graphql.py` provider is a direct GitHub GraphQL client, not a GitHub MCP client.
- So if the intent was "use GitHub MCP", the repo is not doing that at all.

## Root Cause Summary

1. Branch fan-out multiplies latency across the whole provider plan.
2. The paid SERP semaphore only caps concurrency; it does not reduce provider count.
3. Bright Data is expensive and zero-yield in the sampled period.
4. Reddit and Gemini waste time on repeated failures and breaker thrash.
5. HackerNews and GitHub GraphQL are often low-yield relative to their cost.
6. Rerank adds a real sequential tail.
7. The quality/judge analytics path is currently broken in the live DuckDB sink.

## Recommended Fixes

Priority 1:
- Reduce provider fan-out per branch.
- Make `serp_paid` selection explicit, not unconditional.
- Treat Bright Data as opt-in or single-engine, not a default multi-engine fan-out.

Priority 2:
- Move Reddit and HackerNews out of the always-on intent path.
- Gate GitHub GraphQL by intent and query form.
- Surface provider-health failures sooner than `DEBUG`.

Priority 3:
- Fix the analytics sink schema so `rerank_candidates`, `search_quality_scores`, and `judge_evaluations` exist in the live database.
- Stop swallowing `compute_search_quality` failures silently.
- Repair OTLP export so traces are available outside DuckDB.

