# Grafana Dashboards for kindly-web-search-mcp

This directory contains production-ready, importable Grafana dashboard JSONs
designed specifically for the observability instrumentation added in 2026.

## Quick Import

**Via UI:**
1. Grafana → Dashboards → Import
2. Upload JSON or paste content
3. Select Prometheus data source (the one fed by OTel metrics)
4. Set variables: `service`, `environment`

**Via API (example):**
```bash
curl -X POST \
  -H "Authorization: Bearer $GRAFANA_API_KEY" \
  -H "Content-Type: application/json" \
  https://your-org.grafana.net/api/dashboards/db \
  -d @grafana/dashboards/kindly-mcp-overview-dashboard.json
```

## Dashboards

- `kindly-mcp-overview-dashboard.json` — Golden signals (rate, errors, duration) + health
- `kindly-mcp-pipeline-dashboard.json` — Rewrite, multi-provider, merge, rerank
- `kindly-mcp-providers-dashboard.json` — Per-provider latency, success, contribution
- `kindly-mcp-content-dashboard.json` — Extraction stages, browser vs HTTP, fallbacks
- `kindly-mcp-cache-dashboard.json` — Hit ratios, semantic effectiveness, cost savings
- `kindly-mcp-quality-dashboard.json` — Developer quality loop for search result yield,
  domain diversity, query length, RRF/rerank score distributions, rewrite mix, and
  semantic-cache freshness signals

All dashboards use the current metric names emitted by `telemetry.py` and the normalized Prometheus label keys exported from those attributes.

## UX/DX Intent

Use the overview, providers, content, cache, and pipeline dashboards for operational
debugging: is the MCP running, are tools failing, are providers slow, and which stage
is broken.

Use the quality dashboard for continuous improvement: are queries too long/noisy, is
rewrite routing changing behavior, are providers producing useful candidate volume, is
RRF/rerank producing separable scores, is diversity collapsing, and are semantic-cache
hits fresh enough to trust.

Current dashboards still do not replace an evaluation harness. Answer quality, citation
correctness, source usefulness, manual ratings, and curated known-query scorecards need
additional analytics events or eval metrics before they can be represented honestly in
Grafana.

## Variables (common)

- `service` (default: web-search-mcp)
- `environment`
- `provider` (regex multi-select)

## Requirements

- Metrics coming from OpenTelemetry (direct or via Alloy) into a Prometheus-compatible backend (Grafana Cloud Mimir or local).
- Time series with the labels used in the instrumentation (provider.name, content.stage, etc.).

Generated as part of the 2026 observability enhancement.
