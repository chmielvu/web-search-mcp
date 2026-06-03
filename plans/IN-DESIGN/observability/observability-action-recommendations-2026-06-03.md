# Observability Action Recommendations

Snapshot time: `2026-06-03T06:52:57+02:00`

Use this as the implementation checklist for the observability stack.

## P0

- Make `agentic.research.completed` the only canonical success event for `agentic_web_research`.
- Keep `tool.agentic_web_research.response` only as a transport boundary event, not a success counter.
- Add a regression test that fails if a tool emits duplicate terminal success semantics.
- Emit error logs at real error severity for `tool.*.error` events.
- Preserve structured fields on the log line, but do not rely on `INFO` severity for failures.

## P1

- Add a cache-hit-rate dashboard panel for exact, semantic, and page cache separately.
- Add query-class segmentation for cache metrics so one-off research and repeated query families are not mixed.
- Track cache lookup latency next to hit rate so overhead and value are visible together.
- Add a run-timeline marker for simple runs that do not currently show rewrite, rerank, fetch, or answer stages.
- Distinguish `search-only`, `fetch-heavy`, `agentic`, and `answer-producing` runs in the timeline.

## P2

- Repair or replace the failing `find_slow_requests` investigation helper.
- Document a direct Tempo TraceQL fallback for slow-request triage and make it part of the operator runbook.
- Add alerts for sustained p95 increases on `web_search`, `get_content`, and `agentic_web_research`.
- Add alerts for spikes in `tool.agentic_web_research.error`, upstream HTTP 429/503/403 spans, and missing terminal events.

## P3

- Reduce the need for manual trace/log stitching by exposing a standard trace drill-down path from the main dashboards.
- Ensure every dashboard panel has a trace or log pivot that lands on the relevant trace IDs and run keys.
- Add a small set of canonical "what happened" panels for:
  - terminal success rate,
  - terminal error rate,
  - cache hit rate,
  - slowest tool runs,
  - upstream status-code mix.

## Acceptance Criteria

- One logical run maps to one canonical success event and one canonical error event.
- Cache effectiveness can be read from the dashboard without querying DuckDB manually.
- The slow-request investigation path works every time or has a documented direct replacement.
- Logs, traces, and dashboards all use the same run identifiers and terminal semantics.
