# Observability Guide

This document describes the OpenTelemetry instrumentation, Grafana Cloud integration, and dashboards available for the kindly-web-search-mcp-server.

## Overview

The server emits traces, metrics, and structured logs following a three-layer model:

- **Layer 1 (Transport)**: MCP/JSON-RPC request handling
- **Layer 2 (Tool Execution)**: Search pipeline, content resolution, caching, scraping
- **Layer 3 (Agentic)**: Query policy, rewrite quality, result usefulness signals

All signals are designed to work excellently with **Grafana Cloud Application Observability** (RED metrics + traces + logs correlation).

## Quick Start (Grafana Cloud)

### 1. Environment Variables (Recommended)

```bash
# Standard OTEL (works everywhere)
export OTEL_EXPORTER_OTLP_ENDPOINT="https://otlp-gateway-prod-us-east-0.grafana.net/otlp"
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic $(echo -n '123456:glc_...' | base64)"

# Windows / convenience (recommended for pwsh users)
export GRAFANA_CLOUD_INSTANCE_ID="123456"
export GRAFANA_CLOUD_API_KEY="glc_..."
export GRAFANA_CLOUD_OTLP_ENDPOINT="https://otlp-gateway-prod-us-east-0.grafana.net/otlp"

# Sampling (default 0.15)
export KINDLY_OTEL_SAMPLING_RATIO="0.15"

# Optional Prometheus scrape endpoint for Alloy
export KINDLY_PROMETHEUS_ENABLED="true"
export KINDLY_PROMETHEUS_PORT="9090"
```

The server automatically detects `GRAFANA_CLOUD_*` variables and builds the correct Basic auth header.

### 2. Initialization

Telemetry is initialized automatically very early in `server.py` (before any HTTP clients or heavy imports). No manual `init_telemetry()` call is required in normal usage.

### 3. What Gets Emitted

- Traces: Full pipeline spans (`search.orchestrator.*`, `content.fetch_pipeline`, provider calls, rerank stages, browser tasks)
- Metrics: `web_search_content_resolutions_total`, `web_search_content_fallback_total`, `web_search_content_errors_total`, `web_search_provider_calls_total`, `web_search_query_rewrite_total`, `web_search_rerank_total`, `web_search_cache_requests_total`, `mcp_tool_invocations_total`, and related duration histograms.
- Logs: Structured JSON (when telemetry is enabled) with trace context for Loki correlation.

## Dashboards

Six ready-to-import dashboards live in `grafana/dashboards/`:

1. **kindly-mcp-overview-dashboard.json** — Golden signals (RPS, error rate, p95 latency by tool)
2. **kindly-mcp-providers-dashboard.json** — Per-provider health and contribution
3. **kindly-mcp-content-dashboard.json** — Extraction stages, fallback rates, browser usage, error breakdown (most important for debugging the 2026-05-14 health report issues)
4. **kindly-mcp-cache-dashboard.json** — Hit ratios by type (exact/semantic/page), semantic similarity distribution
5. **kindly-mcp-pipeline-dashboard.json** — Query rewrite policy, rerank stages, RRF merge performance
6. **kindly-mcp-quality-dashboard.json** — Quality assessment cockpit for result yield, query length, domain diversity, rewrite mix, RRF/rerank score distributions, diversity removals, and semantic-cache freshness signals

**Import instructions** are in `grafana/README.md`.

### Quality Assessment Scope

The Grafana dashboards are split intentionally:

- **Operational dashboards** answer whether the MCP is healthy and which stage is failing.
- **Quality dashboard** answers whether search behavior is improving or degrading from a developer's point of view.
- **Offline analytics / evals** are still required for source usefulness, answer correctness, citation correctness, and manual quality ratings.

The current Prometheus metrics support continuous tuning of query rewrite behavior,
provider result yield, domain diversity, RRF/rerank score separability, semantic-cache
similarity/TTL, and content-resolution reliability. They do not yet directly score
final answer quality for `gemini_search` / `perplexity_search`, citation grounding,
or per-query relevance labels.

## DuckDB and MotherDuck Analytics

The metrics dashboards are not the source of truth for hard values. The MCP writes
quality-relevant observability events into local DuckDB at
`.kindly/analytics/search_events.duckdb` by default.

Captured raw payloads now include:

- original query, normalized query, research goal, rewrite variants, and final rewritten queries
- provider branch summaries, merged results, result URLs, snippets, domains, scores, and warnings
- exact/semantic/page cache lookup and store events, including hit/miss/expiry status and TTL context
- provider health transitions such as success, cooldown entry, and reset state
- content-stage resolution, fallback, error, and markdown classification events with status/reason context
- rerank stage summaries and top reranked results
- fetched URLs, normalized URLs, fetched URLs after redirect, fetch backend, metadata, links, full returned page content, and summaries
- fetch window offsets, lengths, returned character counts, total character counts, page character counts, and word counts
- `gemini_search` / `perplexity_search` answers, source URLs, grounding chunks, and structured results
- eval run/case/observation payloads plus LLM quality scores for offline assessment

Local analytics can be disabled with `KINDLY_ANALYTICS_ENABLED=false` or redirected
with `KINDLY_ANALYTICS_DUCKDB_PATH`.

Run a deterministic local report against the DuckDB file:

```powershell
.\.venv\Scripts\kindly-web-search-mcp-server.exe analytics-report --report provider-performance --duckdb-path .kindly/analytics/search_events.duckdb
```

The report command installs the local shared analytics views before executing the
named report. Available report names include:

- `provider-performance`
- `cache-hit-rates`
- `rewrite-variant-quality`
- `fetch-quality`
- `error-taxonomy`
- `candidate-survival`
- `eval-quality-summary`

For an allowlisted, read-only analytics query interface, use:

```powershell
.\.venv\Scripts\kindly-web-search-mcp-server.exe analytics-query --question "fetch quality" --duckdb-path .kindly/analytics/search_events.duckdb
```

`analytics-query` routes natural-language questions into a bounded SQL shape over
local DuckDB or the attached MotherDuck schema. It only accepts recognized topics
(`cache`, `provider`, `middleware/session`, `error`, `eval`, `fetch`, `content`,
`recent events`) and returns the generated SQL plus JSON-safe rows.

### Sync to MotherDuck

Run a one-shot sync:

```powershell
$env:MOTHERDUCK_TOKEN="..."
$env:KINDLY_MOTHERDUCK_DATABASE="my_db"
.\.venv\Scripts\kindly-web-search-mcp-server.exe sync-analytics
```

If the local venv was created before the wrapper console script existed, call the
CLI function directly:

```powershell
.\.venv\Scripts\python.exe -c "from kindly_web_search_mcp_server.cli import main; main(['sync-analytics'])"
```

Run it as a 5-minute loop:

```powershell
.\.venv\Scripts\kindly-web-search-mcp-server.exe sync-analytics --loop --interval-seconds 300
```

Use an existing MotherDuck database name for `KINDLY_MOTHERDUCK_DATABASE`; the
sync creates the `kindly_analytics` schema inside that database. On Windows, the
sync defaults DuckDB's extension directory to `.kindly/duckdb_extensions` and
sets gRPC CA roots from `certifi` when `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH` is not
already set. MotherDuck currently rejected DuckDB `1.5.3` in live testing, so the
project pins DuckDB below `1.5.3`.

The sync creates:

- `kindly_analytics.analytics_event_raw` - append-only raw event mirror
- `kindly_analytics.vw_quality_events` - Grafana-friendly drill-down view
- `kindly_analytics.vw_run_timeline` - per-run timeline view
- `kindly_analytics.vw_events` - normalized base event view for local and cloud analytics
- `kindly_analytics.vw_candidate_survival` - candidate stage survival analysis
- `kindly_analytics.analytics_event_daily` - refreshed daily summary table
- `kindly_analytics.vw_eval_provider_quality` - eval quality summary by suite and tool
- `kindly_analytics.vw_eval_fetch_quality` - eval fetch quality by backend/status
- `kindly_analytics.eval_quality_daily` - refreshed eval summary table
- `kindly_analytics.analytics_sync_state` - sync heartbeat and source/target row counts

Grafana Cloud cannot install the unsigned MotherDuck DuckDB datasource plugin
through the public plugin catalog. For Grafana Cloud, use the built-in
PostgreSQL datasource against MotherDuck's Postgres endpoint instead:

- type: `grafana-postgresql-datasource`
- host: `pg.us-east-1-aws.motherduck.com:5432`
- database: `my_db`
- user: `postgres`
- password: `MOTHERDUCK_TOKEN`
- SSL mode: `require`

The quality dashboard's MotherDuck panels are written against the synced
`kindly_analytics` schema and are datasource-variable driven, so they work with
this Postgres endpoint path while keeping the same cloud tables/views.

## Sampling & Cost Control

- Head-based sampling is controlled by `KINDLY_OTEL_SAMPLING_RATIO` (default 15%).
- Attribute values are aggressively truncated via `KINDLY_OBSERVABILITY_MAX_TEXT_CHARS` and `KINDLY_OBSERVABILITY_MAX_ITEMS` to keep cardinality low.
- Recommendation: 10-20% in production, 100% in staging/dev.

## Troubleshooting

**No data in Grafana Cloud Application Observability**
- Confirm `OTEL_EXPORTER_OTLP_ENDPOINT` is set before the process starts.
- Check that the token has `MetricsPublisher` + `TracesPublisher` roles.
- Look for the startup log line containing `"event": "telemetry.startup"`.

**High cardinality / cost**
- Lower `KINDLY_OTEL_SAMPLING_RATIO`.
- Increase truncation limits only when debugging specific queries.

**Windows / pwsh auth issues**
- Prefer the three `GRAFANA_CLOUD_*` variables over manually constructing Base64 headers.

**Missing browser or content stage data**
- The browser path only runs on hard fallbacks. Exercise URLs that require JS (e.g., heavy SPAs) to populate the "browser_nodriver" stage.

## Semantic Conventions Used

See constants in `telemetry.py`:
- `search.*`, `provider.*`, `content.stage`, `content.final_stage`, `content.status`, `rerank.stage`, `cache.*`, `mcp.*`

Prometheus/Grafana label names are normalized from those attributes, so the dashboards use forms like `provider_name`, `content_stage`, `content_final_stage`, `cache_hit`, and `gen_ai_tool_name`.

These match the attribute names used in the provided Grafana dashboards.

## Related Files

- `src/kindly_web_search_mcp_server/telemetry.py` — Core initialization and recording helpers
- `src/kindly_web_search_mcp_server/utils/observability.py` — Safe attribute truncation
- `settings.py` — All `KINDLY_OTEL_*` and `GRAFANA_CLOUD_*` configuration

---
Maintained as part of the 2026 observability enhancement effort.
