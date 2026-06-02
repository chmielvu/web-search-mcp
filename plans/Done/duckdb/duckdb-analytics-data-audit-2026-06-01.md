# DuckDB Analytics Data Audit — What's Stored & What's Missing

**Date:** 2026-06-01
**Context:** Comprehensive audit of the `search_events` DuckDB table and the observability pipeline feeding it. Identifies every stored event, every gap, and recommends the minimum additions needed for self-observing analytics.

---

## 1. Current Storage: Complete Event Inventory

All events pass through `_persist_analytics_event()` at `utils/observability.py:258`. The filter at line 263 only persists events matching prefixes `query.rewrite.`, `search.`, `provider.`, `tool.`.

The `search_events` table at `analytics/duckdb_store.py:48` has 18 fixed columns:

```
event_id, event_name, recorded_at, run_key, tool_name, phase,
query, normalized_query, research_goal, provider, model,
duration_ms, input_count, output_count, trace_id, span_id,
cache_hit, payload_json
```

Plus a `payload_json` JSON string blob for unstructured fields.

### 1.1 `query.rewrite.*` Events

| Event | Source | Fixed columns populated | Key fields in payload_json |
|-------|--------|------------------------|---------------------------|
| `query.rewrite.completed` | `search/query_rewrite.py:269` | event_name, query, normalized_query, research_goal, model, duration_ms, input_count (variant_count), output_count (final_query_count) | variants[{kind, target, query, weight, why}], final_queries[], providers_requested[], active_provider_names[], models_used[], policy, intent |
| `query.rewrite.error` | `search/query_rewrite.py:289` | event_name, query, normalized_query, research_goal | error_type, error_message, policy, intent, final_queries[] (fallback) |

**Fixed-column gap:** `model` gets concatenated string like `"mistral-small-2603,cerebras,groq"` — useless for per-model analytics. Variants are invisible to SQL without `json_extract()`.

### 1.2 `search.*` Events

| Event | Source | Fixed columns populated | Key fields in payload_json |
|-------|--------|------------------------|---------------------------|
| `search.orchestrator.plan` | `search/orchestrator.py:127` | event_name, query, normalized_query, research_goal, input_count (num_queries), output_count (num_variants) | final_queries[], query_variants[], active_providers[], per_query_k, search_options, rewrite_policy, providers_requested[] |
| `search.orchestrator.branches` | `search/flow_observability.py:88` | event_name, query | branches[{index, query, providers[], weight, result_count, provider_counts{}, domain_counts{}, results[], top_results[]}], branch_count, total_candidate_count |
| `search.single_query.response` | `search_instrumented.py:295` | event_name, query, input_count (num_results_requested), output_count (merged_result_count) | results[{title,link,snippet,domain,providers[],provider_count,score}], active_providers[], providers_used[] |
| `search.orchestrator.response` | `search/orchestrator.py:261` | event_name, query, normalized_query, research_goal, input_count, output_count | results[] (final window), merged_results[] (all merged), rewrite_policy, rewrite_reason, unique_domains, providers_requested[], providers_used[], warnings[], result_window{offset, limit, has_more, next_offset} |
| `search.merge.summary` | `search/merge_observability.py:31` | event_name, duration_ms, input_count, output_count | rrf_k, list_weights[], provider_contributions{}, overlap_rate, discarded_count, max_per_host, host_cap_top_k, top_results[], output_host_counts{} |
| `search.rerank.stage` | `rerank/observability.py:35` | event_name, query, duration_ms, input_count, output_count | stage (bi_encoder/cross_encoder/diversity), status, error_type, error_message, model, extra{} |
| `search.rerank.summary` | `rerank/observability.py:50` | event_name, query, provider, model, duration_ms, input_count, output_count | score_threshold, max_score, results[] (reranked), top_results[] |

### 1.3 `provider.*` Events

| Event | Source | Fixed columns populated | Key fields in payload_json |
|-------|--------|------------------------|---------------------------|
| `provider.search.result` | `search_instrumented.py:113` | event_name, query, provider, duration_ms, input_count (requested), output_count (returned) | results[{title, link, snippet, domain, providers[], provider_count, score}] |
| `provider.search.error` | `search_instrumented.py:149` | event_name, query, provider, duration_ms | error_type, error_message |

### 1.4 `tool.*` Events (25 emission points, 8 tools)

| Event | Source | Fixed columns populated | Key fields in payload_json |
|-------|--------|------------------------|---------------------------|
| `tool.web_search.request` | `server.py:598` | event_name, query, normalized_query, research_goal, input_count (num_results) | num_results, result_offset, rewrite_enabled, providers_requested[], providers_key, search_options |
| `tool.web_search.response` (cache_hit=exact) | `server.py:630` | event_name, query, normalized_query, research_goal, cache_hit="exact", output_count | results[], providers_used[], warnings[], result_window |
| `tool.web_search.response` (cache_hit=semantic) | `server.py:676` | event_name, query, normalized_query, research_goal, cache_hit="semantic", output_count | results[], providers_used[], warnings[], result_window |
| `tool.web_search.response` (cache_hit=miss) | `server.py:829` | event_name, query, normalized_query, research_goal, cache_hit="miss", output_count | results[], providers_used[], warnings[], result_window |
| `tool.get_content.request` | `server.py:913` | event_name | url, char_offset, char_length, summary_mode, focus_query, include_metadata, include_links, max_links, strip_selectors |
| `tool.get_content.response` | `server.py:1093` | event_name | input_url, normalized_url, fetched_url, **status**, **source_type**, **fetch_backend**, **content_length**, page_content (full text up to 50K chars), window, metadata, links, continuation_notice, **content_type**, error, summary |
| `tool.batch_get_content.request` | `server.py:1181` | event_name, input_count (url_count) | urls[], max_concurrency, per_item_char_length, total_char_budget, has_cursor, include_metadata, include_links, max_links |
| `tool.batch_get_content.response` | `server.py:1259` | event_name, input_count, output_count | results[{input_url, normalized_url, fetched_url, status, source_type, fetch_backend, content_type, page_content, window, metadata, links, error}], success_count, error_count, has_more, cursor, total_requested, total_returned, total_chars_returned |
| `tool.discover_links.request` | `server.py:~1320` | event_name | url, max_links, include_external, same_domain_only |
| `tool.discover_links.response` | `server.py:~1360` | event_name, output_count | links[{url, text, domain, internal}] |
| `tool.gemini_search.request` | `server.py:1414` | event_name, query, research_goal | structured_output |
| `tool.gemini_search.response` | `server.py:1445` | event_name, query, research_goal | answer (full text), structured_result, web_search_queries[], grounding_chunks[], error |
| `tool.gemini_search.error` | `server.py:1475` | event_name, query, research_goal | error_type, error_message |
| `tool.perplexity_search.request` | `server.py:1539` | event_name, query, research_goal | depth |
| `tool.perplexity_search.response` | `server.py:1576` | event_name, query, research_goal | answer (full text), sources[], model, depth |
| `tool.perplexity_search.error` | `server.py:1614` | event_name, query, research_goal | error_type, error_message |
| `tool.youtube_transcript.request` | `server.py:~1760` | event_name | video_id_or_url, language, translate_to, format |
| `tool.youtube_transcript.response` | `server.py:~1790` | event_name | video_id, transcript_text, language, is_translated, duration_seconds, format |
| `tool.youtube_search.request` | `server.py:~1850` | event_name, query, input_count | num_results |
| `tool.youtube_search.response` | `server.py:~1880` | event_name, query, output_count | results[] |
| `tool.academic_search.request` | `server.py:1985` | event_name, query, normalized_query, input_count (limit) | limit, sources[], sources_key, year_from, year_to, fields_of_study[], venue, open_access_only, sort |
| `tool.academic_search.response` (cached) | `server.py:2026` | event_name, query, cache_hit="exact" or "semantic", output_count | sources_used[], results[] |
| `tool.academic_search.response` (miss) | `server.py:2154` | event_name, query, normalized_query, cache_hit="miss", output_count | sources_used[], results[], warnings[] |
| `tool.academic_search.error` | `server.py:2174` | event_name, query | error_type, error_message |
| `tool.composio_similarlinks.response` | composio_tools.py | event_name | url, num_results, results[] |
| `tool.quick_web_search.response` | composio_tools.py | event_name, query, model | answer, citations[], web_search_queries[] |

---

## 2. What Never Enters `search_events` (Storage Gaps)

### 2.1 Cache Operations — ZERO events

All three cache tiers call OTEL `record_cache_lookup()` but write **no DuckDB events**. This is the single largest gap.

**Files involved:** `cache/query_cache.py`, `cache/semantic_cache.py`, `cache/page_cache.py`

| Missing event | Would contain | Why critical |
|---------------|---------------|-------------|
| `cache.lookup.exact` | query, result (hit/miss/expired), duration_ms, **age_seconds**, **ttl_seconds**, providers_key | Hit rate trends, TTL optimization per query type |
| `cache.lookup.semantic` | query, **similarity_score**, **content_type**, result (hit/miss/below_threshold/expired), duration_ms, **age_seconds**, **ttl_seconds**, **vector_distance**, search_type | Content-type-aware TTL tuning, similarity threshold optimization |
| `cache.lookup.page` | url, **extraction_method**, **word_count**, result (hit/miss/expired), **age_seconds** | Content freshness analysis, extraction method quality comparison |
| `cache.store.exact` | query, ttl_seconds, providers_key | Cache growth rate monitoring |
| `cache.store.semantic` | content_type, ttl_seconds | Content type distribution in cache |
| `cache.store.page` | url, extraction_method, word_count | Extraction method distribution |

### 2.2 Middleware — ZERO events

**Files involved:** `middleware/rate_limits.py`, `middleware/expensive_tool_protection.py`, `middleware/session_tracking.py`

| Missing event | Would contain | Why critical |
|---------------|---------------|-------------|
| `middleware.rate_limit.throttled` | tool_name, tier (cheap/expensive), wait_duration_ms, session_id | Capacity planning, rate limit tuning |
| `middleware.rate_limit.acquired` | tool_name, tier, tokens_remaining | Usage pattern analysis |
| `middleware.expensive_tool.blocked` | tool_name, session_id, attempt_count | Steering effectiveness measurement |
| `middleware.expensive_tool.allowed` | tool_name, session_id, attempt_count | Perplexity usage tracking |

### 2.3 Session Lifecycle — ZERO events

Session tracking (`middleware/session_tracking.py`) is **in-memory only**, TTL-based, never persisted.

| Missing event | Would contain | Why critical |
|---------------|---------------|-------------|
| `session.started` | session_id, client_id | Client usage patterns |
| `session.ended` | session_id, duration_seconds, total_calls, tools_used[] | Session cost and complexity analysis |
| `session.heartbeat` | session_id, tool_calls_this_session, session_age_seconds | Active session monitoring |

### 2.4 Content Resolution Stage Details — final only

The `tool.get_content.response` event stores the final result, but the **intermediate fallback chain** is invisible.

**File involved:** `content/resolver.py` (staged fallback: StackExchange → GitHub Issues → GitHub Discussions → Wikipedia → arXiv → HTTP extract → browser)

| Missing event | Would contain | Why critical |
|---------------|---------------|-------------|
| `content.stage.attempt` | url, stage_name, status (success/failed), duration_ms, backend_used | Fallback chain analysis, which stages are most successful |
| `content.stage.fallback` | url, from_stage, to_stage, reason | Fallback rate and reason analysis |

### 2.5 Provider Circuit Breaker — ZERO events (OTEL only)

**File involved:** `search/provider_health.py`, `telemetry.py` (`record_circuit_breaker_state`, `record_circuit_breaker_event`)

| Missing event | Would contain | Why critical |
|---------------|---------------|-------------|
| `circuit.opened` | provider, consecutive_failures, cooldown_seconds | Root cause analysis for provider outages |
| `circuit.closed` | provider, consecutive_successes | Recovery tracking |
| `circuit.half_open` | provider, probe_result | Circuit breaker tuning |

### 2.6 Error Classification — ZERO events (OTEL only)

The structured error system (`errors.py:303` — `classify_error()`, `StructuredToolError`) classifies errors into categories but never persists classification data.

| Missing event | Would contain | Why critical |
|---------------|---------------|-------------|
| `tool.error.classified` | tool_name, error_category (rate_limit/auth/network/content/config/unknown), provider, guidance_message | Error trend analysis by category, provider reliability |

---

## 3. What Is Stored But Not Analyzed Locally

The `payload_json` column contains rich nested data, but local DuckDB has **zero analytical queries or views**. The MotherDuck views (`vw_provider_results`, `vw_merged_results`, et al.) exist only in the cloud.

These fields in `payload_json` are invisible to local SQL:

- **Results arrays** (`.results[]`): Present in 12+ event types. Contains `title, link, snippet, domain, providers[], provider_count, score`
- **Provider contributions** (`.provider_contributions{}`): From merge summaries, keyed by provider name with contribution counts
- **Rewrite variants** (`.variants[]`): From query rewrite. Contains `kind, target, query, weight, why`
- **Domain counts** (`.output_host_counts{}`, `.domain_counts{}`): From merge and branch summaries
- **Branch summaries** (`.branches[]`): Per-branch results with provider breakdowns
- **Answer content** (`.answer`): Full text from gemini/perplexity responses
- **Page content** (`.page_content`): From get_content — full markdown up to 50K chars
- **Window metadata** (`.window`, `.result_window`): Pagination metadata with offsets and has_more flags
- **Content quality signals** (`.status, .source_type, .fetch_backend, .content_type`): Present in get_content events but never analytically scored
- **Error types and messages** (`.error_type, .error_message`): Present but unstructured

---

## 4. Specific Data That Should Be Added

### 4.1 Cache Events (Priority 1 — currently invisible)

**In `cache/query_cache.py`, `lookup()` method:**

```python
# After existing record_cache_lookup() call
emit_observability_event(logger, "cache.lookup.exact",
    lookup_type="exact",
    query=normalized_query,
    cache_hit="true" if found else "false",
    duration_ms=round(duration * 1000, 3),
    age_seconds=age_seconds if found else None,
    ttl_seconds=ttl_seconds,
    providers_key=providers_key,
    result="hit" if found else ("expired" if age_seconds > ttl_seconds else "miss"),
)
```

**In `cache/semantic_cache.py`, `get_semantic_cache()`:**

```python
# After existing record_semantic_cache_lookup() call
emit_observability_event(logger, "cache.lookup.semantic",
    lookup_type="semantic",
    query=query,
    cache_hit="true" if found else "false",
    similarity_score=best_similarity,
    content_type=content_type.value if found else None,
    ttl_seconds=ttl_seconds if found else None,
    age_seconds=age_seconds if found else None,
    search_type="hybrid" if use_hybrid else "vector",
    vector_distance=best_row.get("_distance") if best_row else None,
    result=("hit" if found
            else "expired" if (best_row and age_seconds > ttl_seconds)
            else "below_threshold" if best_row
            else "miss"),
)
```

**In `cache/page_cache.py`, `lookup()`:**

```python
# After existing record_cache_lookup() call
emit_observability_event(logger, "cache.lookup.page",
    lookup_type="page",
    url=canonical_url,
    cache_hit="true" if found else "false",
    age_seconds=age_seconds if found else None,
    extraction_method=extraction_method if found else None,
    word_count=word_count if found else None,
    result="hit" if found else ("expired" if (row and age_seconds > ttl_seconds) else "miss"),
)
```

### 4.2 Cache Store Events (for growth monitoring)

**In `cache/query_cache.py`, `store()`:**

```python
emit_observability_event(logger, "cache.store.exact",
    query=normalized_query,
    ttl_seconds=ttl_seconds,
    providers_key=providers_key,
)
```

**In `cache/semantic_cache.py`, `set_semantic_cache()`:**

```python
emit_observability_event(logger, "cache.store.semantic",
    content_type=content_type.value,
    ttl_seconds=ADAPTIVE_TTL_SECONDS.get(content_type),
)
```

**In `cache/page_cache.py`, `store()`:**

```python
emit_observability_event(logger, "cache.store.page",
    url=canonical_url,
    extraction_method=extraction_method,
    word_count=word_count,
)
```

### 4.3 Content Quality Signals (add to existing `get_content.response` event)

**In `server.py:1093`, add to the existing `emit_tool_observability_event` call:**

```python
word_count=len(response["page_content"].split()),  # NEW
page_title=response.get("metadata", {}).get("title", ""),  # NEW
domain=response.get("metadata", {}).get("domain",
    urlparse(response["input_url"]).netloc),  # NEW
extraction_method=response["fetch_backend"],  # NEW (already in payload but explicit)
```

### 4.4 Session Context (add to every `tool.*.request` event)

**In `server.py`, add to request-phase `emit_tool_observability_event` calls:**

```python
session_id=getattr(ctx, 'session_id', None),  # NEW
client_id=getattr(ctx, 'client_id', None),    # NEW
```

### 4.5 Error Classification (add to every `tool.*.error` event)

**In `server.py` error handlers, before `emit_tool_observability_event(logger, ..., "error")`:**

```python
classified = classify_error(exc)
# Add to event:
error_category=classified.error_type,  # NEW: rate_limit|auth|network|content|config|unknown
provider=classified.provider,          # NEW: which provider (if provider-specific)
```

### 4.6 Circuit Breaker Events

**In `search/provider_health.py`, add to circuit state transitions:**

```python
emit_observability_event(logger, f"circuit.{new_state}",  # "circuit.opened" etc.
    provider=provider_name,
    consecutive_failures=failures,
    cooldown_seconds=cooldown,
)
```

### 4.7 Rate Limit Events

**In `middleware/rate_limits.py`, `_TokenBucketLimiter.acquire()`:**

```python
# After successful acquire with wait time:
if wait_seconds > 0.01:  # Actually had to wait
    emit_observability_event(logger, "middleware.rate_limit.throttled",
        tier="expensive" if self._rps < 1.0 else "cheap",
        wait_duration_ms=round(wait_seconds * 1000, 1),
    )
```

---

## 5. Pre-Built Analytical Queries for Local DuckDB

These queries should live in a new `analytics/queries.py` module, parameterized as Python functions returning DataFrames. They work with **existing data** except where noted.

### 5.1 Provider Performance

```sql
-- Provider latency percentiles (existing data)
SELECT
    provider,
    COUNT(*) AS calls,
    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms,
    PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY duration_ms) AS p99_ms,
    ROUND(p99_ms / NULLIF(p50_ms, 0), 2) AS tail_ratio,
    COUNT(*) FILTER (WHERE event_name = 'provider.search.error') AS errors,
    ROUND(100.0 * errors / NULLIF(COUNT(*), 0), 2) AS error_rate_pct,
    AVG(output_count) FILTER (WHERE event_name = 'provider.search.result') AS avg_results
FROM search_events
WHERE event_name IN ('provider.search.result', 'provider.search.error')
  AND recorded_at >= CURRENT_DATE - INTERVAL '7 days'
GROUP BY provider
ORDER BY p95_ms
```

### 5.2 Cache Hit Rate by Type (requires cache events from §4.1)

```sql
SELECT
    date_trunc('day', recorded_at) AS day,
    json_extract_string(payload_json, '$.lookup_type') AS cache_type,
    COUNT(*) AS total_lookups,
    COUNT(*) FILTER (WHERE cache_hit = 'true') AS hits,
    COUNT(*) FILTER (WHERE cache_hit = 'false') AS misses,
    ROUND(100.0 * hits / NULLIF(hits + misses, 0), 2) AS hit_rate_pct
FROM search_events
WHERE event_name IN ('cache.lookup.exact', 'cache.lookup.semantic', 'cache.lookup.page')
  AND recorded_at >= CURRENT_DATE - INTERVAL '7 days'
GROUP BY 1, 2
ORDER BY 1, 2
```

### 5.3 TTL Optimization — Cumulative Hit Capture (requires cache events from §4.1)

```sql
-- Identifies knee point in cumulative hit capture curve per content type
WITH hit_age AS (
    SELECT
        json_extract_string(payload_json, '$.content_type') AS content_type,
        CAST(json_extract_string(payload_json, '$.age_seconds') AS INTEGER) AS age_seconds,
        cache_hit
    FROM search_events
    WHERE event_name = 'cache.lookup.semantic'
      AND json_extract_string(payload_json, '$.age_seconds') IS NOT NULL
),
bucketed AS (
    SELECT content_type,
        FLOOR(age_seconds / 3600.0) AS hour_bucket,
        COUNT(*) FILTER (WHERE cache_hit = 'true') AS hits,
        COUNT(*) FILTER (WHERE cache_hit = 'false') AS misses
    FROM hit_age GROUP BY 1, 2
),
cumulative AS (
    SELECT content_type, hour_bucket, hits, misses,
        SUM(hits) OVER (PARTITION BY content_type ORDER BY hour_bucket)
        * 100.0 / NULLIF(SUM(hits) OVER (PARTITION BY content_type), 0)
        AS cumulative_capture_pct
    FROM bucketed
)
SELECT content_type,
    MIN(hour_bucket) FILTER (WHERE cumulative_capture_pct >= 90) AS optimal_ttl_hours_90pct,
    MIN(hour_bucket) FILTER (WHERE cumulative_capture_pct >= 95) AS optimal_ttl_hours_95pct,
    MAX(cumulative_capture_pct) FILTER (WHERE hour_bucket <= 168) AS max_capture_7d_pct
FROM cumulative WHERE hour_bucket <= 168
GROUP BY content_type
```

### 5.4 Semantic Cache Threshold Tuning (requires cache events from §4.1)

```sql
SELECT
    ROUND(CAST(json_extract_string(payload_json, '$.similarity_score') AS DOUBLE), 2) AS threshold_bucket,
    COUNT(*) AS lookups,
    COUNT(*) FILTER (WHERE cache_hit = 'true') AS hits,
    ROUND(100.0 * hits / NULLIF(COUNT(*), 0), 2) AS hit_rate_pct,
    AVG(CAST(json_extract_string(payload_json, '$.duration_ms') AS DOUBLE))
        FILTER (WHERE cache_hit = 'true') AS avg_hit_latency_ms
FROM search_events
WHERE event_name = 'cache.lookup.semantic'
  AND json_extract_string(payload_json, '$.similarity_score') IS NOT NULL
GROUP BY 1 ORDER BY 1 DESC
```

### 5.5 Content Extraction Quality (existing data)

```sql
SELECT
    json_extract_string(payload_json, '$.fetch_backend') AS backend,
    json_extract_string(payload_json, '$.source_type') AS source_type,
    json_extract_string(payload_json, '$.status') AS fetch_status,
    COUNT(*) AS fetches,
    ROUND(AVG(LENGTH(json_extract_string(payload_json, '$.page_content')))) AS avg_chars,
    ROUND(AVG(
        CASE json_extract_string(payload_json, '$.status')
            WHEN 'success' THEN 1.0 WHEN 'partial' THEN 0.5 ELSE 0.0
        END
    ), 3) AS reliability_score
FROM search_events
WHERE event_name = 'tool.get_content.response'
  AND recorded_at >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY 1, 2, 3
ORDER BY fetches DESC
```

### 5.6 Domain-Level Fetch Quality (existing data)

```sql
SELECT
    REGEXP_EXTRACT(
        json_extract_string(payload_json, '$.normalized_url'),
        'https?://([^/]+)', 1
    ) AS domain,
    COUNT(*) AS fetches,
    COUNT(*) FILTER (WHERE json_extract_string(payload_json, '$.status') = 'success') AS success,
    COUNT(*) FILTER (WHERE json_extract_string(payload_json, '$.status') = 'error') AS errors,
    ROUND(100.0 * success / NULLIF(COUNT(*), 0), 1) AS success_rate
FROM search_events
WHERE event_name = 'tool.get_content.response'
  AND recorded_at >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY domain HAVING COUNT(*) >= 10
ORDER BY success_rate
```

### 5.7 Full-Request Timeline Reconstruction (existing data)

```sql
WITH timeline AS (
    SELECT
        coalesce(run_key, trace_id, event_id) AS req_id,
        MAX(CASE WHEN event_name = 'query.rewrite.completed' THEN duration_ms END) AS rewrite_ms,
        MAX(CASE WHEN event_name = 'search.rerank.summary' THEN duration_ms END) AS rerank_ms,
        MAX(CASE WHEN event_name = 'search.merge.summary' THEN duration_ms END) AS merge_ms,
        MIN(recorded_at) AS req_start,
        MAX(recorded_at) AS req_end,
        COUNT(DISTINCT provider) FILTER (WHERE event_name = 'provider.search.result') AS provider_count,
        MAX(CASE WHEN cache_hit = 'true' THEN 1 ELSE 0 END) AS cache_hit
    FROM search_events
    WHERE run_key IS NOT NULL
      AND recorded_at >= CURRENT_DATE - INTERVAL '7 days'
    GROUP BY coalesce(run_key, trace_id, event_id)
)
SELECT
    date_trunc('hour', req_start) AS hour,
    COUNT(*) AS requests,
    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY
        EXTRACT(EPOCH FROM req_end - req_start)) AS p50_total_s,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY
        EXTRACT(EPOCH FROM req_end - req_start)) AS p95_total_s,
    AVG(rewrite_ms) AS avg_rewrite_ms,
    AVG(rerank_ms) AS avg_rerank_ms,
    AVG(merge_ms) AS avg_merge_ms,
    AVG(provider_count) AS avg_providers,
    ROUND(100.0 * SUM(cache_hit) / NULLIF(COUNT(*), 0), 1) AS cache_hit_rate_pct
FROM timeline GROUP BY hour ORDER BY hour DESC
```

### 5.8 Pipeline Health Dashboard (existing data)

```sql
SELECT
    date_trunc('hour', recorded_at) AS hour,
    COUNT(*) FILTER (WHERE event_name LIKE 'tool.web_search.response') AS web_search_calls,
    COUNT(*) FILTER (WHERE cache_hit != 'miss' AND event_name LIKE 'tool.web_search.response')
        AS cache_hits,
    COUNT(*) FILTER (WHERE event_name = 'tool.get_content.response') AS content_fetches,
    COUNT(*) FILTER (WHERE event_name = 'tool.gemini_search.response') AS gemini_calls,
    COUNT(*) FILTER (WHERE event_name = 'tool.perplexity_search.response') AS perplexity_calls,
    COUNT(*) FILTER (WHERE event_name LIKE '%.error') AS errors,
    ROUND(100.0 * errors / NULLIF(COUNT(*), 0), 2) AS error_rate_pct,
    ROUND(100.0 * cache_hits / NULLIF(web_search_calls, 0), 2) AS web_search_cache_hit_rate_pct
FROM search_events
WHERE recorded_at >= CURRENT_DATE - INTERVAL '24 hours'
GROUP BY 1 ORDER BY 1 DESC
```

### 5.9 Error Rate Anomaly Detection (existing data, requires error_category from §4.5)

```sql
WITH hourly AS (
    SELECT
        date_trunc('hour', recorded_at) AS hour,
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE event_name LIKE '%.error') AS errors
    FROM search_events
    WHERE recorded_at >= CURRENT_DATE - INTERVAL '7 days'
    GROUP BY hour
),
rolling AS (
    SELECT hour, total, errors,
        ROUND(100.0 * errors / NULLIF(total, 0), 2) AS error_rate_pct,
        AVG(100.0 * errors / NULLIF(total, 0)) OVER (
            ORDER BY hour ROWS BETWEEN 23 PRECEDING AND CURRENT ROW
        ) AS rolling_24h_avg,
        AVG(100.0 * errors / NULLIF(total, 0)) OVER (
            ORDER BY hour ROWS BETWEEN 167 PRECEDING AND CURRENT ROW
        ) AS rolling_7d_baseline
    FROM hourly
)
SELECT *,
    (error_rate_pct > 2.0 * rolling_7d_baseline AND error_rate_pct >= 5.0) AS anomaly_flagged
FROM rolling ORDER BY hour DESC
```

---

## 6. Implementation Priority

| Priority | What | Effort | Value |
|----------|------|--------|-------|
| **P1** | Cache lookup/store events (§4.1, §4.2) | Medium — 3 files | Enables TTL optimization, threshold tuning, hit-rate analytics |
| **P2** | Content quality signals (§4.3) | Low — 1 call site | Enables extraction quality scoring from existing stored data |
| **P3** | Error classification (§4.5) | Low — error handlers | Enables error-category trends, provider reliability analysis |
| **P4** | Session context (§4.4) | Low — request handlers | Enables session-level analytics, usage patterns |
| **P5** | Analytical query module (`analytics/queries.py`) | Medium — new file | Self-observation without MotherDuck/Grafana |
| **P6** | Circuit breaker events (§4.6) | Low — 1 file | Root cause analysis for provider outages |
| **P7** | Rate limit events (§4.7) | Low — 1 file | Capacity planning, rate limit tuning |
| **P8** | Content resolution stage events (§2.4) | Medium — resolver | Fallback chain optimization |

---

## 7. Architecture Note

The three-tier caching system uses **LanceDB** (vector-optimized) while analytics uses **DuckDB** (SQL-optimized). This is the correct separation — LanceDB for embedding search, DuckDB for OLAP. The cache events in §4.1 should write to DuckDB (like all other observability events), not duplicate data. The `lancedb_duckdb` extension (Jan 2026) could in the future bridge these for combined queries, but the event-based approach avoids cross-database dependencies.
