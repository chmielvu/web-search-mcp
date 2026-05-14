# Audit Fixes Design - 2026-05-13

Implementation design for 3 audit findings from web-search-mcp code review.

## Summary

| ID | Finding | Status Before | Fix |
|----|---------|---------------|-----|
| P1.2 | Semantic cache min_score too low (0.82) | Already fixed (0.92) | No action |
| P1.3 | Hyphen regex matches hyphenated words | Already fixed | No action |
| P1.4 | No `include_content` param in web_search | Missing | Add optional param |
| P1.5 | No retry when all variants fail must_keep_terms | Missing | Add retry logic |
| P2.1 | No per-provider circuit breaker | Already implemented | No action |
| P2.2 | No timeout on SingleFlight waiter | Already has timeout | No action |
| P2.3 | Semantic cache write blocks response | Already uses create_task | No action |
| P2.4 | No timeout-specific retry for Jina rerank | Missing | Add retry fallback |

---

## P1.4: Add `include_content` Parameter to `web_search`

### Goal

Reduce round trips by 50% for agents doing single-pass research. Exa and Tavily support inline content extraction.

### Changes

**File: `server.py`**

1. Add parameter to `web_search` signature:
   ```python
   include_content: bool = False,
   ```

2. After `_normalize_lightweight_search_response`, if `include_content=True`:
   - Take top 3 results
   - Fire concurrent HTTP extractions using `http_extract.py` (trafilatura, no browser)
   - Timeout: 10s per URL, 15s total via `asyncio.wait_for`
   - Add `page_content` field to successful extractions
   - Leave `page_content=None` on failure (don't fail entire search)

3. Update tool docstring to describe the parameter behavior.

### Constraints

- HTTP-only extraction (no headless browser) to keep latency practical
- Top 3 results only (balances latency vs utility)
- Extraction failures are silent (null content, not error)
- Concurrent extraction with aggregate timeout

### Testing

- Add test in `test_server.py`: `include_content=True` returns content for top 3
- Add test: extraction timeout leaves `page_content=None`
- Add test: `include_content=False` returns lightweight results unchanged

---

## P1.5: Enforce `must_keep_terms` with Retry

### Goal

Ensure LLM-generated query variants preserve required literals (quoted strings, search operators, versions, error codes).

### Current Behavior

- `query_rewrite_validate.py` has `inject_missing_terms()` that appends missing terms
- Validation functions filter out variants that don't keep required terms
- If all variants fail validation, `_build_plan()` returns empty variants and triggers fallback

### Gap

No retry mechanism when all variants are discarded. The fallback injects terms but loses the LLM's semantic expansion.

### Changes

**File: `query_rewrite.py`**

1. After gathering `keyword_valid` and `neural_valid` from validation:
   ```python
   if not keyword_valid and not neural_valid:
       # Retry once with stronger prompt
       retry_result = await _request_variants_with_enforced_terms(...)
       keyword_valid, neural_valid = retry_result
   ```

2. Max 1 retry. If retry also produces zero valid variants, fall back to original query with `inject_missing_terms`.

**File: `query_rewrite_prompts.py`**

1. Add function `build_retry_messages_with_enforced_terms()`:
   ```python
   def build_retry_messages_with_enforced_terms(
       query: str,
       must_keep_terms: list[str],
       intent: RewriteIntent,
       target: str,
   ) -> list[dict]:
       base_messages = build_query_rewrite_messages(...)
       # Insert explicit instruction after system prompt
       instruction = {
           "role": "system",
           "content": f"CRITICAL: The following terms MUST appear in EVERY query variant:\n"
                      + "\n".join(f"- {term}" for term in must_keep_terms)
                      + "\n\nDo NOT remove, modify, or paraphrase these terms."
       }
       return base_messages[:1] + [instruction] + base_messages[1:]
   ```

### Fallback Path

If retry fails:
```python
return _fallback_plan(
    query,
    policy,
    f"All variants dropped must_keep_terms after retry; original query with terms injected."
)
```

The fallback already uses `inject_missing_terms` in `_build_plan()`.

### Testing

- Add test: all variants fail validation triggers retry
- Add test: retry produces valid variants with required terms
- Add test: retry also fails returns injected original query
- Add test: single valid variant skips retry

---

## P2.4: Jina Rerank Timeout-Specific Retry

### Goal

Handle Jina API timeouts gracefully instead of silently skipping cross-encoder stage.

### Current Behavior

```python
try:
    ranked_indices = await jina_rerank(query, documents, timeout=30.0)
except Exception as e:
    logger.warning(f"Jina rerank failed: {e}, skipping rerank stage")
```

Generic catch means timeout failures are treated same as API errors, no retry.

### Changes

**File: `rerank/core.py`**

Replace generic Exception catch with tiered handling:

```python
stage2_start = time.time()
jina_retry_attempted = False
try:
    ranked_indices = await jina_rerank(query, documents, timeout=30.0)
except asyncio.TimeoutError:
    logger.warning(f"Jina rerank timeout with {len(documents)} docs, retrying with top 5")
    jina_retry_attempted = True
    try:
        reduced_docs = documents[:5]
        ranked_indices = await jina_rerank(query, reduced_docs, timeout=15.0)
        # Map reduced indices back to full candidate list
        candidates = [candidates[i] for i in ranked_indices] + candidates[5:]
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning(f"Jina retry failed: {e}, falling back to bi-encoder order")
        # Proceed with current order (bi-encoder if Stage 1 ran, else input order)
except Exception as e:
    logger.warning(f"Jina rerank error: {e}, skipping rerank stage")
```

### Fallback Behavior

- If Stage 1 (bi-encoder) ran: candidates are already sorted by embedding similarity
- If Stage 1 skipped: candidates are in provider merge order
- Either case: proceed to Stage 3 (diversity) with current order

### Telemetry

Add span attribute:
```python
main_span.set_attribute("rerank.jina_retry", jina_retry_attempted)
```

### Testing

- Add test: timeout triggers retry with 5 docs
- Add test: retry timeout falls back gracefully
- Add test: successful retry maps indices correctly
- Add test: non-timeout exceptions skip retry

---

## Scope Check

These 3 fixes are independent:
- P1.4 touches `server.py` only
- P1.5 touches `query_rewrite.py` and `query_rewrite_prompts.py`
- P2.4 touches `rerank/core.py` only

No cross-file dependencies between fixes. Can implement in any order.

## Files to Modify

1. `src/kindly_web_search_mcp_server/server.py` - P1.4
2. `src/kindly_web_search_mcp_server/search/query_rewrite.py` - P1.5
3. `src/kindly_web_search_mcp_server/search/query_rewrite_prompts.py` - P1.5
4. `src/kindly_web_search_mcp_server/rerank/core.py` - P2.4
5. `tests/test_server.py` - P1.4 tests
6. `tests/test_query_rewrite.py` - P1.5 tests
7. `tests/test_jina_rerank.py` or `tests/test_rerank_core.py` - P2.4 tests

## Implementation Order

Recommended: P2.4 → P1.5 → P1.4

Reason: P2.4 and P1.5 are reliability fixes with smaller scope. P1.4 adds new parameter with more surface area.