# Web Search MCP Observability Design

Comprehensive OpenTelemetry instrumentation following best practices from Grafana Cloud, OTEL semantic conventions, and MCP-specific observability patterns.

## Architecture Overview

### Three-Layer Observability Model (per MCP best practices)

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 3: Agentic Performance                                   │
│  ─────────────────────────────                                   │
│  Task success rate, turns-to-completion, tool hallucination     │
│  Self-correction rate, context coherence                         │
└─────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────┐
│  Layer 2: Tool Execution                                         │
│  ─────────────────────                                           │
│  Provider calls, latency, error rates, result counts            │
│  Cache hit/miss, RRF merge, token usage                          │
└─────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────┐
│  Layer 1: Transport/Protocol                                     │
│  ─────────────────────────                                       │
│  JSON-RPC health, MCP session, connection stability             │
│  Message latency, serialization, transport metrics              │
└─────────────────────────────────────────────────────────────────┘
```

### Signal Types

| Signal | Purpose | Export Target |
|--------|---------|---------------|
| **Traces** | Request flow, provider calls, merge operations | Grafana Cloud Tempo |
| **Metrics** | Provider performance, cache efficiency, throughput | Grafana Cloud Mimir |
| **Logs** | Structured debugging, audit trail, errors | Grafana Cloud Loki |

---

## Semantic Conventions (OTEL Standard)

### HTTP Client Spans (Provider Calls)

Following [OTEL HTTP Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/http/http-spans):

| Attribute | Type | Required | Description |
|-----------|------|----------|-------------|
| `http.request.method` | string | ✓ | GET, POST, etc. |
| `url.full` | string | ✓ | Full request URL |
| `server.address` | string | ✓ | Provider host |
| `server.port` | int | ✓ | Provider port |
| `http.response.status_code` | int | On response | HTTP status |
| `error.type` | string | On error | Exception type or HTTP code |
| `network.protocol.version` | string | Recommended | HTTP/1.1, HTTP/2 |

### MCP Spans (JSON-RPC Operations)

Following emerging [OTEL MCP Semantic Conventions](https://github.com/open-telemetry/semantic-conventions/blob/main/docs/gen-ai/mcp.md):

| Attribute | Type | Condition | Description |
|-----------|------|-----------|-------------|
| `mcp.method.name` | string | Always | `tools/call`, `resources/list` |
| `mcp.server.name` | string | Always | `web-search-mcp` |
| `mcp.session.id` | string | If present | Session identifier |
| `gen_ai.tool.name` | string | For tools/call | Tool invoked |
| `gen_ai.operation.name` | string | For tools/call | `execute_tool` |
| `rpc.system` | string | Always | `jsonrpc` |
| `rpc.jsonrpc.version` | string | Always | `2.0` |

### Custom Search Attributes

Domain-specific attributes for web search operations:

| Attribute | Type | Description |
|-----------|------|-------------|
| `search.query` | string | Search query (truncated to 500 chars) |
| `search.num_results_requested` | int | Requested result count |
| `search.num_results_returned` | int | Actual results returned |
| `search.providers_requested` | string[] | Providers asked to search |
| `search.providers_used` | string[] | Providers that responded |
| `search.merge_algorithm` | string | `rrf_k60` |
| `search.cache_hit` | bool | Cache lookup result |
| `search.cache_type` | string | `exact`, `semantic`, `page` |

### Provider Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `provider.name` | string | searxng, ddg, gemini, tavily, brave, jina |
| `provider.status` | string | success, error, timeout |
| `provider.result_count` | int | Results from this provider |
| `provider.duration_ms` | float | Provider latency |
| `provider.error_type` | string | HTTP_500, TimeoutError, RateLimitError |

---

## Span Hierarchy

```
mcp.session {session_id}
├── mcp.request tools/call {method, tool_name}
│   └── web_search {query, num_results}
│       ├── provider.searxng {provider, query, url}
│       │   └── HTTP GET {http.method, url.full}
│       ├── provider.gemini {provider, query}
│       │   └── HTTP POST {http.method, url.full}
│       ├── rrf_merge {input_lists, output_count}
│       └── rerank {input_count, output_count}
│
├── mcp.request tools/call {method, tool_name}
│   └── get_content {url}
│       ├── content.stackexchange {url}
│       ├── content.github_issue {url}
│       ├── content.wikipedia {url}
│       ├── content.arxiv {url}
│       └── content.http_extract {url}
│           └── HTTP GET {http.method, url.full}
│           └── universal_html {url}
│               └── nodriver.fetch {url}
```

---

## Metrics Specification

### Provider Metrics

```yaml
# Counter: Total provider calls
web_search_provider_calls_total:
  unit: "1"
  description: "Total calls to search providers"
  attributes:
    - provider.name
    - http.response.status_code
    - provider.status
    - provider.error_type

# Histogram: Provider latency
web_search_provider_duration_seconds:
  unit: "s"
  description: "Provider call latency distribution"
  bucket_boundaries: [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10]
  attributes:
    - provider.name
    - http.response.status_code

# Counter: Results per provider
web_search_provider_results_total:
  unit: "1"
  description: "Total results returned by provider"
  attributes:
    - provider.name
```

### Cache Metrics

```yaml
# Counter: Cache requests
web_search_cache_requests_total:
  unit: "1"
  description: "Cache lookup requests"
  attributes:
    - cache.type (exact, semantic, page)
    - cache.hit (true, false)

# Histogram: Cache lookup latency
web_search_cache_duration_seconds:
  unit: "s"
  description: "Cache lookup latency"
  bucket_boundaries: [0.001, 0.005, 0.01, 0.02, 0.05, 0.1]
  attributes:
    - cache.type
```

### Search Metrics

```yaml
# Counter: Total searches
web_search_requests_total:
  unit: "1"
  description: "Total web_search tool invocations"
  attributes:
    - search.providers_used

# Histogram: End-to-end search latency
web_search_duration_seconds:
  unit: "s"
  description: "Complete search pipeline latency"
  bucket_boundaries: [0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60]
  attributes:
    - search.providers_used

# Histogram: RRF merge latency
web_search_merge_duration_seconds:
  unit: "s"
  description: "RRF merge algorithm latency"
  bucket_boundaries: [0.001, 0.002, 0.005, 0.01, 0.02, 0.05]
  attributes:
    - merge.input_lists
    - merge.output_count

# Counter: Tool invocations (MCP standard)
mcp_tool_invocations_total:
  unit: "1"
  description: "MCP tool call count"
  attributes:
    - gen_ai.tool.name
    - provider.status
```

### Content Resolution Metrics

```yaml
# Counter: Content resolution attempts
web_search_content_resolutions_total:
  unit: "1"
  description: "Content resolution by stage"
  attributes:
    - content.stage (stackexchange, github, wikipedia, arxiv, http, nodriver)
    - content.status (success, error, fallback)

# Histogram: Content extraction latency
web_search_content_duration_seconds:
  unit: "s"
  description: "Content extraction latency per stage"
  bucket_boundaries: [0.1, 0.5, 1, 2, 5, 10, 20, 30]
  attributes:
    - content.stage
```

---

## Resource Attributes

Standard OTEL resource attributes for Grafana Cloud Application Observability:

```python
resource_attrs = {
    # Required for Grafana Cloud
    "service.name": "web-search-mcp",
    "service.namespace": "kindly-mcp",
    "service.version": "1.0.8",
    "service.instance.id": f"{hostname}-{pid}",
    
    # Deployment context
    "deployment.environment": os.environ.get("DEPLOYMENT_ENV", "development"),
    
    # Host context
    "host.name": hostname,
    "host.arch": "amd64",
    "host.os.type": "windows",
    
    # Process context
    "process.pid": pid,
    "process.executable.name": "python",
    "process.runtime.name": "python",
    "process.runtime.version": "3.12",
    
    # OTEL SDK info
    "telemetry.sdk.language": "python",
    "telemetry.sdk.name": "opentelemetry",
    "telemetry.sdk.version": "1.20.0",
}
```

---

## Instrumentation Points

### 1. Server Entry (server.py)

```python
# At startup: Initialize telemetry BEFORE any imports
from telemetry import init_telemetry
init_telemetry(service_name="web-search-mcp")

# On each tool call: Create MCP span
with tracer.start_as_current_span(
    f"mcp.request {method}",
    kind=SpanKind.SERVER,
    attributes={
        "mcp.method.name": method,
        "mcp.server.name": "web-search-mcp",
        "rpc.system": "jsonrpc",
        "rpc.jsonrpc.version": "2.0",
    }
) as span:
    # Tool execution
    result = await tool_handler(arguments)
    span.set_attribute("gen_ai.tool.name", tool_name)
```

### 2. Search Orchestrator (search/orchestrator.py)

```python
# Web search operation
with tracer.start_as_current_span(
    "web_search",
    kind=SpanKind.INTERNAL,
    attributes={
        "search.query": query[:500],
        "search.num_results_requested": num_results,
        "search.providers_requested": str(providers),
    }
) as span:
    # Provider calls
    for provider in providers:
        with tracer.start_as_current_span(
            f"provider.{provider}",
            kind=SpanKind.CLIENT,
            attributes={
                "provider.name": provider,
                "search.query": query[:500],
            }
        ) as provider_span:
            results = await search_provider(...)
            provider_span.set_attribute("provider.result_count", len(results))
            provider_span.set_attribute("provider.duration_ms", duration_ms)
            
            # Add results as span events (visible in Grafana)
            for i, result in enumerate(results[:10]):
                provider_span.add_event(
                    f"result.{i}",
                    attributes={
                        "result.title": result.title[:200],
                        "result.url": result.link,
                    }
                )
```

### 3. HTTP Client (provider modules)

Auto-instrumented via `opentelemetry-instrumentation-httpx`:

- All httpx calls automatically create CLIENT spans
- HTTP semantic conventions applied automatically
- Context propagation to downstream services

### 4. Content Resolver (content/resolver.py)

```python
# Staged resolution
for stage in ["stackexchange", "github", "wikipedia", "arxiv", "http_extract", "nodriver"]:
    with tracer.start_as_current_span(
        f"content.{stage}",
        kind=SpanKind.CLIENT,
        attributes={
            "content.stage": stage,
            "url.full": url,
        }
    ) as span:
        content = await resolver_stage(url)
        span.set_attribute("content.status", "success" if content else "fallback")
        span.set_attribute("content.size_bytes", len(content) if content else 0)
```

### 5. RRF Merge (search/merge.py)

```python
with tracer.start_as_current_span(
    "rrf_merge",
    kind=SpanKind.INTERNAL,
    attributes={
        "merge.input_lists": len(result_lists),
        "merge.input_total": total_results,
    }
) as span:
    merged = rrf_merge(result_lists, k=60)
    span.set_attribute("merge.output_count", len(merged))
    span.set_attribute("search.merge_algorithm", "rrf_k60")
```

---

## Structured Logging Schema

JSON logs with trace correlation:

```json
{
  "timestamp": "2026-05-10T10:30:45.123Z",
  "level": "INFO",
  "trace_id": "abc123def456",
  "span_id": "789ghi012",
  "service": {
    "name": "web-search-mcp",
    "version": "1.0.8"
  },
  "mcp": {
    "session_id": "sess_xyz",
    "tool_name": "web_search",
    "method": "tools/call"
  },
  "search": {
    "query": "Claude AI latest news",
    "providers": ["searxng", "gemini"],
    "result_count": 10,
    "duration_ms": 150
  },
  "provider": {
    "name": "searxng",
    "status": "success",
    "latency_ms": 45
  },
  "outcome": {
    "status": "success",
    "cache_hit": false
  }
}
```

---

## Grafana Cloud Dashboards

### Dashboard 1: MCP Server Health

```
Panel: Request Rate
  - mcp_tool_invocations_total rate 5m

Panel: Error Rate
  - mcp_tool_invocations_total{provider.status="error"} rate 5m

Panel: Latency P95
  - histogram_quantile(0.95, web_search_duration_seconds_bucket)

Panel: Active Sessions
  - gauge from trace session spans
```

### Dashboard 2: Provider Performance

```
Panel: Provider Response Time
  - histogram_quantile(0.95, web_search_provider_duration_seconds_bucket)
  by provider.name

Panel: Provider Success Rate
  - rate(web_search_provider_calls_total{provider.status="success"}[5m])
  / rate(web_search_provider_calls_total[5m])

Panel: Results Per Provider
  - rate(web_search_provider_results_total[5m]) by provider.name
```

### Dashboard 3: Cache Efficiency

```
Panel: Cache Hit Rate
  - rate(web_search_cache_requests_total{cache.hit="true"}[5m])
  / rate(web_search_cache_requests_total[5m])
  by cache.type

Panel: Cache Latency
  - histogram_quantile(0.95, web_search_cache_duration_seconds_bucket)
```

---

## Alerting Rules (Prometheus)

```yaml
groups:
  - name: web_search_mcp_alerts
    rules:
      # Layer 1: Protocol errors
      - alert: HighMCPErrorRate
        expr: |
          rate(mcp_tool_invocations_total{provider.status="error"}[5m])
          / rate(mcp_tool_invocations_total[5m]) > 0.05
        for: 10m
        annotations:
          summary: "MCP tool error rate > 5%"
      
      # Layer 2: Provider failures
      - alert: ProviderDown
        expr: |
          rate(web_search_provider_calls_total{provider.status="error"}[10m]) > 0
          and rate(web_search_provider_calls_total[10m]) > 10
        for: 5m
        annotations:
          summary: "Provider {{ $labels.provider_name }} failing"
      
      # Layer 2: Slow providers
      - alert: SlowProvider
        expr: |
          histogram_quantile(0.95, 
            rate(web_search_provider_duration_seconds_bucket[5m])
          ) > 5
        for: 10m
        annotations:
          summary: "Provider P95 latency > 5s"
      
      # Layer 2: No results
      - alert: ZeroResults
        expr: |
          rate(web_search_provider_results_total[15m]) == 0
          and rate(web_search_provider_calls_total{provider.status="success"}[15m]) > 5
        for: 15m
        annotations:
          summary: "Provider returning zero results"
      
      # Layer 3: Search latency
      - alert: SlowSearch
        expr: |
          histogram_quantile(0.99, 
            rate(web_search_duration_seconds_bucket[5m])
          ) > 30
        for: 5m
        annotations:
          summary: "Search P99 latency > 30s"
      
      # Cache efficiency
      - alert: LowCacheHitRate
        expr: |
          rate(web_search_cache_requests_total{cache.hit="true"}[1h])
          / rate(web_search_cache_requests_total[1h]) < 0.5
        for: 1h
        annotations:
          summary: "Cache hit rate < 50%"
```

---

## Implementation Checklist

### Phase 1: Core Telemetry Module (Done ✓)
- [x] OTEL SDK initialization
- [x] OTLP HTTP exporter to Grafana Cloud
- [x] HTTPX auto-instrumentation
- [x] Resource attributes
- [x] Basic provider metrics
- [x] **NEW**: RRF merge metrics (discarded, overlap, provider contribution, scores)
- [x] **NEW**: Query rewrite metrics (policy, variants, duration)
- [x] **NEW**: Reranking metrics (3 stages, relevance scores, diversity removals)
- [x] **NEW**: Semantic cache metrics (similarity distribution, content type, TTL)
- [x] **NEW**: Circuit breaker metrics (state per provider, events)
- [x] **NEW**: All 7 tools' detailed metrics (web_search, get_content, gemini_search, perplexity_search, youtube_transcript, youtube_search, batch_get_content)
- [x] **NEW**: Enhanced result events with RRF score and providers
- [x] **NEW**: Query rewrite variant events
- [x] **NEW**: RRF merge detail events
- [x] **NEW**: Rerank score events
- [x] Structured logging bridge (OTLP log export)

### Phase 2: Integration - Search Instrumentation (Pending)
- [ ] Integrate spans into search/orchestrator.py
- [ ] Add provider call spans with semantic conventions
- [ ] Add RRF merge spans with details
- [ ] Add reranking spans with scores
- [ ] Add query rewrite spans with variants

### Phase 3: Integration - Content Resolution (Pending)
- [ ] Integrate resolver pipeline spans in content/resolver.py
- [ ] Add stage-specific metrics with extraction_method
- [ ] Add fallback tracking

### Phase 4: Integration - MCP Protocol (Pending)
- [ ] Integrate JSON-RPC spans in server.py
- [ ] Add session tracking
- [ ] Add tool invocation metrics with details
- [ ] Add error categorization

### Phase 5: Dashboards & Alerts (Pending)
- [ ] Create Grafana dashboards
- [ ] Configure alert rules
- [ ] LLM-as-judge evaluation metrics

---

## Configuration

### Environment Variables

```bash
# Grafana Cloud OTLP
OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp-gateway-prod-eu-west-2.grafana.net/otlp
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic%20<token>

# Service identity
OTEL_SERVICE_NAME=web-search-mcp

# Optional Prometheus endpoint
KINDLY_PROMETHEUS_PORT=9090

# Sampling (production)
OTEL_TRACES_SAMPLER=parentbased_traceidratio
OTEL_TRACES_SAMPLER_ARG=0.1

# Deployment context
DEPLOYMENT_ENV=production
```

### Dependencies

```toml
observability = [
    "opentelemetry-api>=1.20.0",
    "opentelemetry-sdk>=1.20.0",
    "opentelemetry-exporter-otlp-proto-http>=1.20.0",
    "opentelemetry-instrumentation-httpx>=0.40b0",
    "opentelemetry-exporter-prometheus>=0.40.0",
    "opentelemetry-instrumentation-logging>=0.40b0",
    "prometheus-client>=0.20.0",
]
```

---

## References

- [OpenTelemetry Python Docs](https://opentelemetry.io/docs/languages/python/)
- [OTEL HTTP Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/http/http-spans)
- [OTEL MCP Semantic Conventions](https://github.com/open-telemetry/semantic-conventions/blob/main/docs/gen-ai/mcp.md)
- [Grafana Cloud Application Observability](https://grafana.com/docs/grafana-cloud/monitor-applications/application-observability/)
- [ToolHive MCP Observability](https://github.com/stacklok/toolhive/blob/main/docs/observability.md)
- [MCP Server Observability Best Practices](https://zeo.org/resources/blog/mcp-server-observability-monitoring-testing-performance-metrics)