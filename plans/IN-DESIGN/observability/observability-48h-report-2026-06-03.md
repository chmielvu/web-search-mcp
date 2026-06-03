# 48-Hour Observability Report

Snapshot time: `2026-06-03T06:52:57+02:00`

Analysis window: `2026-06-01T06:52:57+02:00` to `2026-06-03T06:52:57+02:00`

Data sources:

- Local DuckDB analytics store: `.kindly/analytics/search_events.duckdb`
- Grafana Loki datasource: `grafanacloud-logs`
- Grafana Tempo datasource: `grafanacloud-traces`

Notes on method:

- I used DuckDB for counts, latency aggregates, and run-level summaries.
- I used Tempo traces for end-to-end timing and span-level causality.
- I used Loki logs for error-line confirmation and process-instance context.
- `find_slow_requests` failed internally in Grafana MCP, so slow-path analysis uses direct Tempo trace search instead.
- Loki pattern analysis did not find a broad recurring error pattern in the window, so the failure analysis is based on concrete error lines and trace bodies rather than a generic anomaly signature.

## Executive Summary

The system was active and broadly healthy in the last 48 hours. The workload was dominated by standard search and fetch traffic, with a smaller but important agentic lane that is materially more expensive than the rest of the system. The main conclusion is not "the service is failing"; it is "the service is doing real work, and the cost is concentrated in a few expensive stages and a small cluster of agentic failures."

Key takeaways:

- 3,319 analytics events were recorded in the window, spanning 975 run timelines and 291 unique queries.
- The standard search pipeline is busy and mostly successful: `web_search`, `get_content`, rerank, rewrite, and provider health events all show sustained activity.
- The cache is effectively cold in this window: 95 lookups, 93 misses, 2 expired lookups, and 44 stores. It is not currently providing much reuse.
- Query rewrite and rerank are real latency contributors, not just cheap bookkeeping steps.
- `agentic_web_research` is the long-tail cost center. Its average recorded duration is far above the rest of the pipeline, and its slowest traces are dominated by upstream provider latency and retries.
- The observed agentic errors are concentrated, not diffuse. Most of them came from one process instance over a short period, which points more toward process/runtime mismatch or stale execution than a systemic platform outage.
- Loki did not surface a recurring error pattern across the service name; the errors are discrete and task-specific.

## Workload Overview

### Overall volume

| Metric | Value |
| --- | ---: |
| Total analytics events in window | 3,319 |
| Events on 2026-06-01 | 1,836 |
| Events on 2026-06-02 | 1,337 |
| Events on 2026-06-03 partial day | 146 |
| Run timelines | 975 |
| Unique run queries | 291 |
| Average run duration | 11.39 s |
| Run duration p95 | 53.12 s |
| Run duration max | 176.21 s |

Interpretation:

- The workload is not a trickle. It is a sustained multi-stage search and research system with a meaningful tail of long-running runs.
- The 2026-06-03 volume is partial because the snapshot is early in the day; it should not be compared 1:1 with the full prior days.
- The run timeline distribution is skewed toward very short or partially-instrumented runs, but the p95 and max show a real long tail that matters operationally.

### Core pipeline events

| Event family | Count | What it means |
| --- | ---: | --- |
| `provider.search.result` | 565 | Raw provider result emission |
| `search.merge.summary` | 322 | Merge/RRF aggregation summaries |
| `search.rerank.summary` | 155 | Reranking step summaries |
| `query.rewrite.completed` | 92 | Query rewrite completions |
| `search.cache.lookup` | 95 | Cache lookup attempts |
| `search.cache.store` | 44 | Cache writes |
| `provider.health.success` | 185 | Provider health checks passing |
| `provider.health.cooldown` | 99 | Provider cooldown management events |
| `provider.health.reset` | 10 | Provider health resets |

Interpretation:

- The merge and rerank stages are active and are not edge-case features.
- Provider health management is active and looks operational rather than pathological: successes outnumber cooldowns, and resets are present but not dominant.
- The cache is being exercised, but it is not yet acting like a strong reuse layer.

## Tool Usage Mix

### Request / response / error counts

| Tool | Requests | Responses | Errors | Canonical completions |
| --- | ---: | ---: | ---: | ---: |
| `web_search` | 241 | 169 | 0 | n/a |
| `get_content` | 141 | 131 | 0 | n/a |
| `agentic_web_research` | 34 | 21 | 10 | 14 |
| `batch_get_content` | 21 | 20 | 0 | n/a |
| `gemini_search` | 16 | 16 | 0 | n/a |
| `academic_search` | 10 | 10 | 0 | n/a |
| `discover_links` | 6 | 6 | 0 | n/a |
| `perplexity_search` | 2 | 1 | 1 | n/a |

Important nuance:

- `agentic_web_research` emits both `tool.agentic_web_research.response` and `agentic.research.completed`.
- For success analysis, `agentic.research.completed` is the better canonical completion marker. The 21 response events overstate completed outcomes; 14 completions is the cleaner success count.

### Daily breakdown

| Day | Tool | Requests | Responses | Errors | Total rows |
| --- | --- | ---: | ---: | ---: | ---: |
| 2026-06-01 | `web_search` | 182 | 133 | 0 | 315 |
| 2026-06-01 | `get_content` | 102 | 97 | 0 | 199 |
| 2026-06-01 | `batch_get_content` | 21 | 20 | 0 | 41 |
| 2026-06-01 | `gemini_search` | 12 | 12 | 0 | 24 |
| 2026-06-01 | `academic_search` | 10 | 10 | 0 | 20 |
| 2026-06-01 | `discover_links` | 4 | 4 | 0 | 8 |
| 2026-06-01 | `perplexity_search` | 2 | 1 | 1 | 4 |
| 2026-06-02 | `web_search` | 59 | 36 | 0 | 139 |
| 2026-06-02 | `get_content` | 39 | 34 | 0 | 83 |
| 2026-06-02 | `agentic_web_research` | 21 | 15 | 10 | 67 |
| 2026-06-02 | `gemini_search` | 4 | 4 | 0 | 14 |
| 2026-06-02 | `discover_links` | 2 | 2 | 0 | 4 |
| 2026-06-03 | `agentic_web_research` | 13 | 6 | 0 | 46 |

Interpretation:

- 2026-06-01 was dominated by standard search and fetch traffic.
- 2026-06-02 introduced the bulk of the agentic error activity.
- 2026-06-03 is still partial, but agentic traffic continued.
- The workload mix is evolving toward richer research flows, not just simple search.

## Provider Mix

### Search providers

| Provider | Result events |
| --- | ---: |
| `searxng` | 168 |
| `ddg` | 166 |
| `composio_llm_search` | 130 |
| `gemini` | 101 |

### Health / control providers

| Provider | Health events |
| --- | ---: |
| `searxng` | 67 |
| `brave` | 50 |
| `ddg` | 43 |
| `composio_llm_search` | 39 |
| `gemini` | 31 |
| `p1` | 20 |
| `tavily` | 19 |

### Rerank provider

| Provider | Rerank summaries | Avg duration | p95 duration |
| --- | ---: | ---: | ---: |
| `voyage` | 155 | 6.62 s | 18.18 s |

Interpretation:

- The search layer is genuinely multi-provider. `searxng` and `ddg` dominate, but `composio_llm_search` and `gemini` are substantial contributors rather than rare fallbacks.
- Reranking is a single-provider path in this window (`voyage`), so any systemic slowdown there hits all search flows equally.
- Provider diversity is healthy, but it also means the system is exposed to heterogeneous latency and partial-failure behavior.

## Performance Analysis

### Stage-level latency hotspots

| Event / stage | Count | Avg | p95 | Max |
| --- | ---: | ---: | ---: | ---: |
| `provider.search.result` | 565 | 5.26 s | 17.04 s | 56.99 s |
| `search.cache.lookup` | 95 | 3.19 s | 11.20 s | 21.54 s |
| `search.rerank.summary` | 155 | 6.62 s | 18.18 s | 51.49 s |
| `query.rewrite.completed` | 92 | 12.06 s | 30.50 s | 39.12 s |
| `tool.agentic_web_research.response` | 21 | 26.03 s | 99.11 s | 150.31 s |
| `agentic.research.completed` | 14 | 26.89 s | 131.69 s | 150.31 s |

Interpretation:

- Query rewrite is expensive enough to matter on user-visible latency.
- Rerank is also substantial; it is not a negligible post-processing step.
- Cache lookups are not cheap in this window, which makes the low hit rate more painful.
- Agentic research is a different class of workload. It is not just "slower search"; it is a materially more expensive orchestration mode.

### Run-timeline shape

| Metric | Value |
| --- | ---: |
| Runs with 0 rewrite / 0 rerank / 0 fetch / 0 answer markers | 620 |
| Runs with 1 rewrite / 1 rerank / 0 fetch / 0 answer markers | 50 |
| Runs with 0 rewrite / 0 rerank / 2 fetch / 0 answer markers | 111 |
| Runs with 0 rewrite / 1 rerank / 0 fetch / 0 answer markers | 105 |
| Runs with 0 rewrite / 0 rerank / 1 fetch / 0 answer markers | 30 |
| Runs with 0 rewrite / 0 rerank / 0 fetch / 1 answer marker | 17 |

Interpretation:

- A large portion of runs are still simple enough that they do not carry the richer stage markers.
- The instrumentation is useful, but not every run is equally richly annotated. That means you should use traces and raw events together, not rely on a single view.

## Cache Behavior

### Lookup summary

| Lookup status | Count |
| --- | ---: |
| `miss` | 93 |
| `expired` | 2 |

### Cache totals

| Metric | Value |
| --- | ---: |
| Cache lookups | 95 |
| Cache stores | 44 |
| Cache hits observed in this window | 0 |

Interpretation:

- The cache is not currently providing a meaningful hit rate in this window.
- If the expectation is that agent queries are mostly unique, this is acceptable.
- If the expectation is reuse across repeated or normalized query families, then the cache is either too cold, too narrow, or not normalized aggressively enough.
- The expensive part is not "the cache lookup exists"; it is "the cache lookup is mostly not amortizing anything."

## Tempo Trace Findings

### Representative successful search trace

Trace ID: `06776ee584c35e7d1c39ca8da4c866b4`

What happened:

- Root span: `tools/call web_search`
- Root duration: `15.68 s`
- Service stats: 51 spans matched, 5 errors inside the trace
- Search providers used: `searxng`, `ddg`, `gemini`, `composio_llm_search`
- Result count: 40
- Rerank final count: 6

What this means:

- The pipeline can succeed even when individual upstream calls fail.
- This is important: not every 4xx/5xx inside the trace is a user-visible failure.
- The system is doing partial-failure recovery correctly in the common search path.

### Representative content-fetch trace

Trace ID: `d30b1a27312cf13f21a78abd3ee2a003`

What happened:

- Root span: `tools/call get_content`
- Root duration: `40.88 s`
- The internal fetch pipeline span was effectively instantaneous (`0.15 ms`)
- The actual HTTP GET to the source URL took `7.83 s`

What this means:

- The local fetch pipeline is not the bottleneck.
- The latency is dominated by remote site behavior and content delivery.
- Slow `get_content` runs should be interpreted as "the target is slow or awkward", not "the local fetch stack is broken".

### Representative slow agentic trace

Trace ID: `9768b1cf598731502045dec4471ee666`

What happened:

- Root span: `tools/call agentic_web_research`
- Root duration: `59.36 s`
- Multiple NanoGPT/OpenAI-compatible calls returned HTTP 503 before later success
- Semantic Scholar returned HTTP 429
- Jina rerank returned HTTP 403
- HF embedding calls took about `5 s` and `0.33 s`

What this means:

- The agentic lane is dominated by upstream service variability.
- The local orchestration did not spend most of its time computing locally; it spent it waiting on external model/search services.
- This is the main reason agentic research is a special budget class, not just another search tool.

### Longest successful agentic trace

Trace ID: `1718c8ac9caf6b1e3d783bab68c26748`

What happened:

- Root duration: `150.31 s`
- Canonical completion event: `agentic.research.completed`
- Tool calls recorded: 4
- Sources recorded: 20
- Knowledge graph nodes: 36

What this means:

- Long runtime does not automatically mean failure.
- Some agentic tasks are simply expensive but complete successfully.
- These should be treated as a distinct workload class with their own budget and timeout policy.

## Loki Log Findings

### Error pattern analysis

Result:

- Loki error-pattern detection found no broad recurring pattern for `service_name=web-search-mcp` in the window.

Interpretation:

- The service is not generating a single dominant spammy failure signature.
- The error surface is mostly task-specific and trace-specific.

### Concrete error lines

Observed error families:

| Error family | Count | Notes |
| --- | ---: | --- |
| `tool.agentic_web_research.error` with `RuntimeError` | 6 | Concentrated on process instance `DESKTOP-7FDB3EC-17400` |
| `tool.agentic_web_research.error` with `InternalServerError` | 4 | Concentrated on process instance `DESKTOP-7FDB3EC-47784` |
| `tool.perplexity_search.error` with `HTTPError` | 1 | Timeout after 30 s |
| `tool.error.classified` for `analytics_query` | 2 | Classified as `unknown` |

Representative Loki trace IDs from the error burst:

- `2e949a7b460595d059d72b5e5dd83446`
- `411b77e4448125fea4e5fa6a05dbef88`
- `70506863c0ddaebfb36d2b65c1d6110e`
- `efff40b16f2f2ec629d5576b3013b3b6`
- `89c29bc89a55df61fd76b07559d9d685`

What this means:

- The logs show a concentrated burst of agentic failures, not a broad service-wide failure.
- The errors are tied to specific research questions and to specific process instances.
- That pattern is consistent with stale runtime state, mixed code versions, or a bad upstream provider window, rather than a persistent platform bug affecting every request.

## Conclusions

1. The system is functioning as a real multi-stage search/research platform, not just a thin search proxy.
2. The user-facing cost is concentrated in query rewrite, rerank, and agentic orchestration, not in the happy-path request plumbing.
3. The cache is not yet amortizing traffic in a meaningful way.
4. Agentic research is the dominant long-tail latency class and needs a distinct budget policy.
5. The errors that do exist are clustered and specific, which is good news: there is no sign of a broad, repeating failure storm.
6. Observability coverage is good enough to distinguish "slow because upstream is slow" from "slow because the local pipeline is broken."

## Recommendations

Priority order:

1. Treat `agentic_web_research` as a budgeted workload class.
   - Add explicit time or tool-call budgets.
   - Surface partial progress sooner.
   - Prefer graceful partial completion over waiting for every upstream branch to resolve.

2. Reassess the cache strategy.
   - If queries are intentionally unique, accept the cold cache.
   - If reuse is expected, improve normalization and keying so the lookup layer can actually pay off.
   - Track hit rate explicitly in dashboards so the cache is judged by reuse, not just by activity.

3. Treat rewrite and rerank as first-class latency stages.
   - They are not incidental overhead.
   - Consider fast paths for short or obvious queries.
   - Watch these stages separately from search result latency, because they can dominate the total even when providers are healthy.

4. Keep process hygiene tight.
   - The error burst was concentrated on a small number of PIDs / service-instance IDs.
   - Make sure deploys actually retire old instances so traces and logs do not blend stale code with current code.

5. Keep using trace IDs as the primary drill-down key.
   - Loki did not produce a broad pattern signature.
   - The best debugging unit here is still a trace, not a log line.

6. Use `agentic.research.completed` as the canonical success counter.
   - `tool.agentic_web_research.response` is useful, but it overstates completed runs.
   - This matters if you build success-rate dashboards or SLOs.

## Appendix: Selected Evidence

### Trace IDs worth keeping

- Successful search trace: `06776ee584c35e7d1c39ca8da4c866b4`
- Long content fetch trace: `d30b1a27312cf13f21a78abd3ee2a003`
- Slow agentic trace: `9768b1cf598731502045dec4471ee666`
- Long successful agentic trace: `1718c8ac9caf6b1e3d783bab68c26748`

### Selected error trace IDs

- `2e949a7b460595d059d72b5e5dd83446`
- `411b77e4448125fea4e5fa6a05dbef88`
- `70506863c0ddaebfb36d2b65c1d6110e`
- `efff40b16f2f2ec629d5576b3013b3b6`
- `89c29bc89a55df61fd76b07559d9d685`

### Quick reading guide

- If you want to understand normal throughput, start with the workload overview and provider mix.
- If you want to understand why a user waited, start with the performance section and the three representative traces.
- If you want to understand failures, start with the Loki section and the error trace IDs.
- If you want to decide what to improve first, start with the recommendations.
