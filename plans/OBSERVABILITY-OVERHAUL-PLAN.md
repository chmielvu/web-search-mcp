---
name: Observability Overhaul Plan (Grafana LGTM Stack)
description: Refined implementation plan for production-grade OpenTelemetry observability targeting Grafana Cloud (Loki, Tempo, Mimir)
type: project
---

# Web Search MCP — Observability Overhaul Plan (Grafana LGTM Stack)

**Status**: Ready for Implementation  
**Created**: 2026-05-11  
**Updated**: 2026-05-11  
**Target**: Grafana Cloud (Loki + Tempo + Mimir)

---

## Executive Summary

After code review, discovered:
1. **LoggingInstrumentor ALREADY configured** in `telemetry.py:440` — P1-1 was already done
2. **search_instrumented.py exists with full instrumentation** but NOT imported
3. **BUG confirmed**: `query_rewrite.py:439-443` uses wrong attribute names (`type`/`text` vs `kind`/`query`)
4. **No root span** in web_search tool — breaks trace continuity

---

## Grafana LGTM Stack Requirements

### Loki (Logs)
- JSON format with `trace_id` and `span_id` fields
- LogQL correlation: `{job="web-search-mcp"} |= "trace_id=<TRACE_ID>"`
- LoggingInstrumentor injects these into Python logging automatically

### Tempo (Traces)
- OTLP format (already configured)
- Requires proper parent-child relationships (currently broken)
- trace_id format: 32-char hex (OTEL standard)

### Mimir/Prometheus (Metrics)
- Snake_case naming (current telemetry.py follows this)
- Histogram buckets for latency P50/P95/P99

---

## Phase 0: Critical Fixes (Immediate, <30 min)

### P0-1: Enable Instrumented Search Module ⚠️ CRITICAL

**Location**: `orchestrator.py:17`

**Current**:
```python
from . import search_single_query  # Imports from search/__init__.py (non-instrumented)
```

**Fix**:
```python
from ..search_instrumented import search_single_query  # Instrumented version
```

**Grafana Impact**: Provider spans now visible in Tempo trace waterfall

---

### P0-2: Add Root Span to web_search Tool ⚠️ CRITICAL

**Location**: `server.py:351-535` (web_search tool function)

**Current**: No span creation — cache lookups happen before any instrumentation

**Fix**: Wrap entire tool execution in root span using telemetry helper

```python
from ..telemetry import create_mcp_tool_span, set_span_success, set_span_error

async def web_search(...):
    with create_mcp_tool_span("web_search") as span:
        span.set_attribute("search.query", query[:500])
        span.set_attribute("search.num_results_requested", num_results)
        span.set_attribute("search.rewrite_enabled", str(rewrite).lower())
        
        try:
            # ... existing logic
            set_span_success(span, result_count=len(response.results))
            return response
        except Exception as e:
            set_span_error(span, e)
            raise
```

**Grafana Impact**: All child spans (providers, merge, rewrite) now have parent context

---

### P0-3: Fix Variant Attribute Names ⚠️ BUG

**Location**: `query_rewrite.py:439-443`

**Current (BUG)**:
```python
span.add_event(f"rewrite.variant.{i}", attributes={
    "variant.type": getattr(variant, 'type', 'unknown'),  # QueryVariant has 'kind', not 'type'
    "variant.text": getattr(variant, 'text', str(variant))[:100],  # QueryVariant has 'query', not 'text'
})
```

**Fix**:
```python
span.add_event(f"rewrite.variant.{i}", attributes={
    "variant.kind": variant.kind,  # Correct field name
    "variant.query": variant.query[:100],  # Correct field name
    "variant.why": variant.why[:100] if variant.why else "",
})
```

**Grafana Impact**: Variant queries visible in Tempo trace events

---

## Phase 1: Log-Trace Correlation (Partially Done)

### P1-1: LoggingInstrumentor ✅ ALREADY DONE

**Status**: Configured in `telemetry.py:440`

```python
LoggingInstrumentor().instrument(set_logging_format=True)
```

Python logging now includes `trace_id` and `span_id` in format.

---

### P1-2: Structlog Integration for Loki JSON Format

**Problem**: Python logging format is not ideal for Loki queries

**Fix**: Use structlog for structured JSON logs

```python
# utils/structured_logging.py
import structlog
from opentelemetry import trace

def configure_structlog():
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            add_trace_context,  # Inject trace_id/span_id
            structlog.processors.JSONRenderer(),  # Loki expects JSON
        ],
    )

def add_trace_context(logger, method_name, event_dict):
    span = trace.get_current_span()
    if span and span.is_recording():
        ctx = span.get_span_context()
        event_dict["trace_id"] = format(ctx.trace_id, '032x')  # 32-char hex for Tempo
        event_dict["span_id"] = format(ctx.span_id, '016x')  # 16-char hex
    return event_dict
```

**Grafana Impact**: LogQL queries work directly: `{job="web-search-mcp"} | json | trace_id="<TRACE_ID>"`

---

## Phase 2: Quality Metrics (Medium, 2-4 hours)

### P2-1: Query Length Distribution

**Why**: Understand keyword pile-on patterns from LLM queries

```python
# telemetry.py - add histogram
query_length_histogram = meter.create_histogram(
    name="web_search_query_length_chars",
    unit="chars",
    explicit_bucket_boundaries_advisory=[10, 20, 50, 100, 200, 500],
)

# Record in query_rewrite.py
query_length_histogram.record(len(query), {"policy": policy.mode})
```

---

### P2-2: Domain Diversity Gauge

**Why**: Detect when results are too homogeneous

```python
# After merge in orchestrator.py
unique_domains = len(set(r.domain for r in merged if r.domain))
span.set_attribute("search.domain_diversity", unique_domains)
```

---

## Implementation Priority (Revised)

| Phase | Task | Status | Effort | Grafana Impact |
|-------|------|--------|--------|----------------|
| P0-1 | Enable search_instrumented | ✅ DONE | 5 min | HIGH - provider spans visible |
| P0-2 | Add root span | ✅ DONE | 15 min | HIGH - trace continuity |
| P0-3 | Fix variant attrs | ✅ DONE | 5 min | HIGH - correct data in traces |
| P1-1 | LoggingInstrumentor | ✅ ALREADY DONE | 0 min | Logs have trace_id |
| P1-2 | Structlog integration | ✅ DONE | 45 min | MEDIUM - better Loki queries |
| P2-1 | Query length histogram | ✅ DONE | 15 min | MEDIUM - query patterns |
| P2-2 | Domain diversity | ✅ DONE | 10 min | MEDIUM - result quality |

---

## Verification After Implementation

```bash
# 1. Check trace continuity in Grafana Tempo
# Query trace by trace_id from logs → see full waterfall with provider spans

# 2. Check log-trace correlation in Loki
# LogQL: {job="web-search-mcp"} | json | trace_id="<TRACE_ID>"
# Should return logs matching the trace

# 3. Check variant attributes in Tempo
# query_rewrite span should show events with variant.kind, variant.query

# 4. Check provider spans
# Each provider should have child span under root web_search span
```

---

## Files to Modify

1. **orchestrator.py:17** — Change import to search_instrumented
2. **server.py** — Add root span wrapper in web_search tool
3. **query_rewrite.py:439-443** — Fix attribute names