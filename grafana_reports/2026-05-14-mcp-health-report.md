# Grafana MCP Health Report

**Report Period:** 2026-05-13 08:00 UTC — 2026-05-14 08:00 UTC (24 hours)
**Generated:** 2026-05-14 08:00 UTC
**Service:** `web-search-mcp` (kindly-mcp namespace)
**Version:** 1.0.8
**Environment:** development

---

## Executive Summary

| Metric | Value |
|--------|-------|
| **Total Log Entries** | 239 |
| **Unique Instances** | 34 process instances |
| **Tool Invocations** | ~166 total (rate: 0.005/sec avg) |
| **Cache Hits** | 414.6 (exact + semantic + page) |
| **Cache Misses** | 0 (page type only recorded) |
| **Errors Logged** | ~50+ trafilatura parsing errors |
| **Warnings Logged** | 9 provider/rerank warnings |

**Overall Health:** ⚠️ **Fair** — Core functionality operational, but content extraction pipeline showing significant error volume.

---

## Tool Invocation Analysis

### Invocation Rates (24h average)

| Tool | Rate (invocations/sec) | Estimated Total |
|------|------------------------|-----------------|
| `batch_get_content` | 0.00062 | ~53 |
| `get_content` | 0.00186 | ~160 |
| `web_search` | 0.00192 | ~166 |
| `gemini_search` | 0 | 0 |

**Total invocations:** ~166 unique tool calls across 4 tools.

### Input/Output Distribution

| Input URLs | Output Results | Count |
|------------|----------------|-------|
| Various (0-9 URLs) | 1-8 results | Multiple combinations |
| 8 URLs | 2 results | Most common batch pattern |

---

## Cache Performance

### Cache Type Distribution

| Cache Type | Cache Hits (24h) | Cache Misses (24h) |
|------------|------------------|-------------------|
| **exact** | 141.7 | 0 (not recorded) |
| **page** | 132.2 | 0 |
| **semantic** | 140.7 | 0 |

**Cache Efficiency:** Excellent. All three cache layers showing positive hit counts with zero recorded misses (misses may not be fully instrumented).

**Cache Hit Ratio Estimate:** ~100% (based on available data, likely inflated due to missing miss metrics)

---

## Error Analysis

### Error Categories

#### 1. Trafilatura Content Extraction Errors (Primary Issue)

**Count:** ~50+ occurrences
**Source:** `kindly_web_search_mcp_server.content.http_extract` → `trafilatura.core`, `trafilatura.utils`
**Error Patterns:**
- `empty HTML tree: None`
- `parsed tree length: 0, wrong data type or not valid HTML`
- `parsed tree length: 1, wrong data type or not valid HTML`
- `discarding data: None`

**Trace IDs:** Multiple traces affected (e.g., `096e7ada39c8e394fe1d9e07754a101f`, `a0fa76ba07fa1f0faaa0fb65c3305ccc`)

**Impact:** Content extraction failing for certain URLs (likely JS-heavy or malformed pages). The universal_html fallback (nodriver browser) should be invoked but appears not always successful.

**Recommendation:** 
- Add telemetry to track fallback success rates
- Consider pre-filtering URLs known to require browser extraction
- Increase browser pool size for high-content-fetch sessions

#### 2. Provider Errors

| Provider | Error | Count |
|----------|-------|-------|
| DDG (DuckDuckGo) | `No results found` | 5 |
| Jina Rerank | `429 Too Many Requests` | 3 |

**DDG Errors:** Empty result sets from DuckDuckGo searches — expected behavior for some queries but indicates potential query formulation issues.

**Jina Rate Limiting:** HTTP 429 errors during rerank stage. System correctly handles by skipping rerank gracefully.

**Recommendation:**
- Implement retry with exponential backoff for Jina 429s
- Add Jina API key rotation or rate limit caching
- Consider local rerank fallback (e.g., sentence-transformers cross-encoder)

#### 3. OTLP Export Timeout

**Error:** `Failed to export metrics batch: HTTPSConnectionPool(host='otlp-gateway-prod-eu-west-2.grafana.net', port=443): Read timed out`

**Impact:** Metrics may be intermittently lost during export. Single occurrence observed.

---

## Instance Activity

**Active Process Instances:** 34 unique PIDs on `DESKTOP-7FDB3EC`

**Most Active Instance:** `DESKTOP-7FDB3EC-28144` — This instance produced the majority of trafilatura errors, indicating a heavy content-fetch session was running on this process.

---

## Log Volume Statistics

| Metric | Value |
|--------|-------|
| Streams | 8 |
| Chunks | 9 |
| Entries | 239 |
| Bytes | 64,510 |

---

## Tracing Observations

Traces are present in logs with span_ids and trace_ids, indicating OpenTelemetry instrumentation is active and correlated. However, direct Tempo trace queries returned no data (potential Tempo query path issue or trace retention settings).

**Sample Trace IDs for Investigation:**
- `096e7ada39c8e394fe1d9e07754a101f` — trafilatura failure chain
- `d75929aad009655c2fd847dace46846d` — Jina rerank 429
- `942594b567ebefa5dafba7182f166cc1` — DDG no results

---

## Recommendations

### Immediate Actions

1. **Trafilatura Error Handling** — Add structured logging to capture which URLs fail extraction. Consider pre-validation before trafilatura.

2. **Jina Rate Limits** — Implement request queuing with rate limit awareness. Cache rerank results for similar queries.

3. **OTLP Timeout** — Increase export timeout or batch size. Consider local buffering.

### Monitoring Improvements

1. **Add `mcp_errors_total` metric** — Currently returns no data. Ensure error counter is properly incremented and exported.

2. **Cache Miss Metrics** — The `cache_hit="false"` metrics show zero for page cache. Verify miss instrumentation.

3. **Tempo Integration** — Verify Tempo datasource query path for trace detail retrieval.

---

## Grafana Resources Identified

| Resource Type | Name/UID | Purpose |
|---------------|----------|---------|
| Loki Datasource | `grafanacloud-logs` | Log aggregation for MCP |
| Loki Datasource | `grafanacloud-alert-state-history` | Alert state logs |
| Loki Datasource | `grafanacloud-usage-insights` | Usage analytics |
| Prometheus Datasource | `grafanacloud-prom` | Metrics storage |
| Prometheus Datasource | `grafanacloud-ml-metrics` | ML-specific metrics |
| Prometheus Datasource | `grafanacloud-usage` | Usage metrics |
| Tempo Datasource | `grafanacloud-traces` | Distributed tracing |

### Available Metrics

- `mcp_tool_invocations_total` — Tool usage counters
- `mcp_errors_total` — Error counters (not populated)
- `mcp_gemini_search_details_total` — Gemini search stats
- `mcp_perplexity_search_details_total` — Perplexity stats
- `mcp_youtube_search_details_total` — YouTube search stats
- `mcp_youtube_transcript_details_total` — Transcript stats
- `web_search_cache_duration_seconds` — Cache latency histogram
- `web_search_cache_requests_total` — Cache request counters

---

## Conclusion

The MCP is **functionally operational** with good cache performance and reasonable tool invocation distribution. The primary concern is the **trafilatura content extraction failure rate** (~50+ errors in 24h), which suggests the HTTP extraction path is failing more often than expected. The browser-based fallback (`universal_html`) should be handling these but needs verification.

**Next Steps:**
- Query specific trace IDs in Tempo for detailed error chains
- Review content resolver fallback logic
- Validate Jina rate limit handling strategy

---

*Report generated via Grafana MCP integration with Claude Code.*