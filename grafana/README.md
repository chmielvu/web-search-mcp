# Grafana Dashboards for kindly-web-search-mcp

Production-ready, importable Grafana dashboard JSONs designed for the web-search-mcp observability stack.

## Dashboards (v2 — 2026-07 Overhaul)

| Dashboard | UID | Purpose |
|-----------|-----|---------|
| **Overview (Golden Signals)** | `kindly-mcp-overview-v2` | Request rate, error rate, latency, cost rate, active providers, cache hit rate |
| **Provider Health & Latency** | `kindly-mcp-providers-v2` | Per-provider latency, success rate, freshness, circuit breaker state |
| **Pipeline (Rewrite/Merge/Rerank)** | `kindly-mcp-pipeline-v2` | Stage latency, candidates through pipeline, score distribution, compression ratio |
| **Content Extraction & Scraping** | `kindly-mcp-content-v2` | Resolution stages, fallback rate, word count, extraction latency |
| **Cache Effectiveness** | `kindly-mcp-cache-v2` | Hit rate by type, lookup latency, evictions, bytes cached |
| **Quality Assessment** | `kindly-mcp-quality-v2` | NDCG@10, judge scores, domain diversity, provider overlap, RRF distribution |
| **Cost & Token Usage** *(NEW)* | `kindly-mcp-cost-v1` | Spend by provider/model/purpose, token usage, cache savings |
| **A/B Experiments** *(NEW)* | `kindly-mcp-ab-experiments-v1` | Variant comparison, conversion rates, p-values, statistical power |

## Data Sources

All dashboards use:
- **Prometheus** (via OpenTelemetry → Grafana Cloud Mimir) for real-time metrics
- **DuckDB analytics** (`search_events.duckdb`) for historical quality metrics (NDCG, judge scores)

Key telemetry attributes:
- `provider.name` — Provider identifier (tavily, brave, searxng, etc.)
- `content.stage` — Extraction stage (stackexchange, github_issue, wikipedia, arxiv, crawl4ai)
- `rerank.stage` — Pipeline stage (bi_encoder, cross_encoder, rankllm)
- `call_purpose` — LLM purpose (rewrite, rerank, judge, embedding)

## Quick Import

**Via Grafana UI:**
1. Dashboards → Import
2. Upload JSON file or paste content
3. Select Prometheus data source
4. Set variables: `service`, `environment`

**Via API:**
```bash
curl -X POST \
  -H "Authorization: Bearer $GRAFANA_API_TOKEN" \
  -H "Content-Type: application/json" \
  https://your-org.grafana.net/api/dashboards/db \
  -d @grafana/dashboards/kindly-mcp-overview-dashboard.json
```

## Panel Patterns

### Golden Signals (Overview)
- Request rate, error rate, latency percentiles
- Cost rate by provider
- Active providers count
- Cache hit rate gauge

### Provider Health
- Per-provider latency timeseries (p50/p95/p99)
- Success rate bar gauge with thresholds
- Provider freshness (seconds since last success)
- Circuit breaker state changes

### Pipeline Stages
- Stage latency breakdown (bi_encoder, cross_encoder, rankllm)
- Candidates funnel (input → output per stage)
- Score distribution by stage
- Compression ratio bar gauge

### Quality Assessment
- NDCG@10 timeseries and histogram
- Judge score distribution (relevance, accuracy, completeness, source quality)
- Domain diversity trend
- Quality tier pie chart (Poor/Fair/Good/Excellent)

### Cost Attribution
- Spend rate by provider (stacked area)
- Token usage by purpose
- Top models by cost table
- Cache savings gauge

### A/B Experiments
- Variant conversion rate comparison
- Judge score by variant
- P-value and statistical power gauges
- Lift calculation

## Variables

Common template variables:
- `$datasource` — Prometheus data source
- `$service` — Service name (default: web-search-mcp)
- `$provider` — Provider regex filter
- `$call_purpose` — LLM call purpose filter
- `$experiment_id` — A/B experiment filter

## Requirements

- Prometheus-compatible backend (Grafana Cloud Mimir or local Prometheus)
- OpenTelemetry metrics pipeline (OTLP → Grafana Cloud)
- Optional: DuckDB analytics for quality metrics

## Design Principles

Based on research from xops-labs/llm-usage-exporter, prabhaharanv/production-hybrid-rag, and nickna/Conduit:

1. **Per-stage latency tracking** — Tag every metric with `stage` label
2. **Provider freshness thresholds** — Green (<600s), yellow (<1800s), red (>1800s)
3. **Cost attribution** — Track by provider × model × purpose
4. **Quality as time series** — NDCG, recall@k, MRR as Prometheus counters
5. **A/B with statistical thresholds** — P-value coloring, power gauges
6. **Budget burn alerts** — Green (<80%), yellow (80-100%), red (>100%)
7. **Dashboard as code** — File-based provisioning, GitOps workflow

Generated as part of the 2026 observability enhancement. Last overhauled: 2026-07-29.