# Observability Stack Review

Snapshot time: `2026-06-03T06:52:57+02:00`

Scope:

- Evaluate the observability system itself, based on the last 48 hours of DuckDB, Grafana Loki, and Grafana Tempo evidence.
- Focus on coverage, signal quality, semantic consistency, drill-down usefulness, and operational recommendations.

## Executive Summary

The observability stack is good enough to analyze the application meaningfully, which is the most important baseline. It captures enough raw data to reconstruct workload mix, trace outliers, and confirm that the system is mostly healthy. However, the stack still has several quality problems that reduce its diagnostic value:

- some important workflows are double-counted or partially duplicated in event semantics,
- cache observability shows activity but not a convincing effectiveness story,
- a notable fraction of runs are not richly annotated with stage markers,
- some logs carry error payloads at `INFO` severity, which weakens triage,
- the canned slow-request investigation path failed internally even though direct Tempo search worked,
- the trace and log layers are useful, but they still require too much manual stitching for a clean operator workflow.

The short version: the stack is useful, but it is not yet self-explaining.

## What the Stack Does Well

### 1. It captures the right major surfaces

Evidence:

- DuckDB recorded 3,319 analytics events in the 48-hour window.
- 975 run timelines were available for aggregation.
- The raw event stream includes search, merge, rerank, rewrite, cache, provider-health, content, session, and agentic tool events.

Why that matters:

- This is enough breadth to analyze real behavior rather than a narrow debug slice.
- The system is instrumented across the major stages that matter for search quality and user latency.

### 2. It gives useful cross-layer correlation

Evidence:

- Trace IDs from Loki can be followed into Tempo traces.
- The Tempo traces expose service instance IDs, span names, HTTP status codes, and nested provider calls.
- DuckDB holds the same trace IDs and run keys for offline aggregation.

Why that matters:

- This is the main strength of the current observability design.
- It supports the classical triage path: metric anomaly -> trace -> log line -> run summary.

### 3. It reveals real performance structure

Evidence:

- `query.rewrite.completed` averages about 12.06 s.
- `search.rerank.summary` averages about 6.62 s.
- `agentic.research.completed` averages about 26.89 s and reaches 150.31 s at the top end.
- `get_content` traces show remote content latency dominating the local fetch pipeline.

Why that matters:

- The stack can already distinguish local orchestration cost from upstream latency.
- That is a strong observability outcome, because it allows better engineering decisions instead of guesswork.

## Where the Stack Is Weak

### 1. Semantic duplication is still visible

Evidence:

- `agentic_web_research` had both `tool.agentic_web_research.response` and `agentic.research.completed` in the same workflow.
- The run timeline and DuckDB summaries had to be normalized so the canonical completion event would not be misread as a second response.

Why that matters:

- If a single logical run can emit multiple completion-like events, dashboards and SLOs will drift.
- This is the kind of issue that makes charts look plausible while silently lying about the real success rate.

Recommendation:

- Keep one canonical success event per logical tool run.
- Treat all other terminal events as auxiliary metadata, not success counters.

### 2. Cache observability shows activity, not value

Evidence:

- 95 cache lookups, 93 misses, 2 expired lookups, 44 stores.
- No meaningful cache-hit story emerged in the 48-hour window.

Why that matters:

- The cache is visible, but its effectiveness is not.
- Without a hit-rate panel and normalization by query class, the cache will remain hard to justify or tune.

Recommendation:

- Add a first-class cache-hit-rate dashboard.
- Break it out by exact, semantic, and page cache.
- Add query-class segmentation so repeated research families do not get mixed with one-off queries.

### 3. Some run classes are still under-instrumented

Evidence:

- 620 runs had `0` rewrite / `0` rerank / `0` fetch / `0` answer markers in the run-timeline summary.

Why that matters:

- The timeline is useful, but it does not yet explain all runs equally well.
- For simple runs, the current view is too sparse to answer basic questions like "what stage actually happened?"

Recommendation:

- Add a small set of canonical stage markers for simple/short flows.
- Distinguish "minimal search" from "multi-stage research" explicitly in the timeline.

### 4. Error severity is too soft in places

Evidence:

- Loki error lines for `tool.agentic_web_research.error` were surfaced with structured metadata reporting `severity_text: INFO`.

Why that matters:

- Error payloads at info level are easy to miss in alerting and log triage.
- This weakens the operational meaning of the logs even when the content is correct.

Recommendation:

- Emit real error logs at error severity.
- Keep structured fields on the log, but do not rely on metadata alone to communicate severity.

### 5. Higher-level investigation tooling is fragile

Evidence:

- Grafana `find_slow_requests` failed internally in this window.
- Direct Tempo TraceQL search worked and returned useful traces.

Why that matters:

- The underlying data is present, but one of the operator conveniences is unreliable.
- That means triage depends too much on manual fallback paths.

Recommendation:

- Treat the fast-path investigations as production tools, not optional conveniences.
- If `find_slow_requests` is not stable, either fix it or remove it from the operational workflow and document the direct TraceQL replacement.

## Observability Recommendations

### Priority 1: Fix event semantics

Actions:

- Keep `agentic.research.completed` as the single canonical success event.
- Ensure all tools have one obvious terminal success event and one obvious terminal error event.
- Add tests that fail if a tool emits duplicate success semantics.

Outcome expected:

- Cleaner success-rate dashboards.
- Less double-counting in DuckDB and Grafana.

### Priority 2: Make cache value visible

Actions:

- Add cache-hit-rate panels.
- Segment by cache type and query class.
- Track lookup latency separately from hit rate.

Outcome expected:

- It will become obvious whether the cache is helping or just adding overhead.

### Priority 3: Improve stage coverage

Actions:

- Add lightweight stage markers for simple runs.
- Annotate whether a run was search-only, fetch-heavy, agentic, or answer-producing.
- Preserve the current detailed markers for complex runs.

Outcome expected:

- `vw_run_timeline` becomes more explanatory and less sparse.

### Priority 4: Harden severity and alerting

Actions:

- Emit error events at actual error severity.
- Add alerts for:
  - elevated `tool.agentic_web_research.error` rate,
  - sustained p95 increase on `web_search`, `get_content`, and `agentic_web_research`,
  - upstream 429/503/403 spikes in traces,
  - dropped or missing terminal events.

Outcome expected:

- Faster detection of genuine regressions.
- Less manual log hunting.

### Priority 5: Make slow-path investigation reliable

Actions:

- Repair or replace the failed `find_slow_requests` path.
- Document a direct TraceQL query for slow-request triage.
- Keep a stable trace drill-down query in the runbook.

Outcome expected:

- Operators can go from alert to trace without trial-and-error.

## What to Measure Next

If the goal is to improve the observability stack further, the next 48-hour review should explicitly track:

- cache hit rate by cache type,
- success/error ratio by canonical terminal event,
- p50/p95/p99 latency by tool,
- upstream status-code distribution from trace spans,
- count of runs with complete stage coverage,
- count of logs emitted at error severity versus info severity,
- frequency of slow-request investigation failures.

## Final Assessment

The stack is already useful enough to support serious analysis. That is a real accomplishment. The next step is not more data volume; it is better semantics, better severity discipline, and better operator ergonomics.

If those improvements land, the same telemetry should become much easier to trust, much easier to alert on, and much cheaper to use during an incident.
