# Phase 2 — Final Updated Implementation Plan

> **Status:** Reconciled 2026-05-29 against current codebase baseline  
> **Based on:** `phase2-v6-final-enhanced-plan.md` + `functiongemma-service-details.md` + current `src/` state + live MCP tool exercise findings

---

## Executive Summary

The original Phase 2 plan proposed a FunctionGemma classifier, budgeted decomposition, enhanced weighted RRF, and orchestrator changes. The **service is deployed** (`functiongemma-service-details.md` confirms Cloud Run live at `https://functiongemma-classifier-373373473125.us-central1.run.app`), but **zero implementation code was written**. Meanwhile, several foundational improvements landed: provider-aware rewrite with community target, intent-specific temperatures, observability envelope, and middleware refinements.

This document reconciles the original plan against the current codebase and integrates findings from the May 2026 MCP tool exercise (25 live tool calls, competitive analysis against Exa/Tavily/Firecrawl/Brave/Omnisearch, arXiV research, Context7 SDK docs).

---

## 1. What Changed: Baseline vs Original Plan Assumptions

| Plan Assumption | Current Reality | Impact |
|---|---|---|
| `query_policy.py` uses `RewriteMode` with `"bypass"` / `"expand"` / `"decompose"` tri-state | **Only two states: `"bypass"` / `"expand"`** (simplified to precision signal detection, no HF backend) | Decomposition path is even more needed — currently no decomposition at all |
| `query_rewrite_prompts.py` contains only basic prompts | **5 prompts exist:** `KEYWORD_CODE`, `KEYWORD_GENERAL`, `KEYWORD_COMPARISON`, `COMMUNITY_SEARCH`, `NEURAL_TASK` (all fully validated against `QueryVariantKind` enum) | Decomposition prompts are additive, not replacements |
| `query_rewrite_models.py` has only basic models | **Current models:** `QueryVariant`, `QueryRewriteOutput`, `QueryRewritePlan` with `COMMUNITY_PROVIDER_NAMES` already defined | `SubQuestion` and `ClassifierOutput` models are additive |
| `merge.py` uses plain RRF without list weights | **Still true.** `merge_search_results()` accepts `provider_weights` but no `list_weights`. RRF k=60 confirmed correct | `list_weights` parameter still needed |
| `orchestrator.py` dispatches variants without sub-questions | **Still true.** `run_web_search()` iterates `rewrite_plan.variants` directly via `_select_providers_for_variant`. No sub-question dispatch | Sub-question dispatch path still needed |
| FunctionGemma Cloud Run service prerequisite | **Service is live** at `https://functiongemma-classifier-373347358125.us-central1.run.app` (confirmed by `test_functiongemma.py` and `functiongemma-service-details.md`) | Ready to build against |
| `pybreaker` dependency | **Not installed** — `pyproject.toml` does not include `pybreaker` | Need to add |
| Cross-encoder reranker exists | **Already implemented** — `orchestrator.py:197-208` lazy-loads `from ..rerank import rerank_results` and reranks against original query | No change needed, already correct |

---

## 2. Phase 2 Implementation — Updated Tasks

### 2.1 FunctionGemma Classifier Client

**Original plan §1 — STATUS: NOT IMPLEMENTED, STILL VALID**

**What to build:** `search/query_classifier_client.py` with:
- HTTP client calling `POST https://functiongemma-classifier-373347358125.us-central1.run.app/generate`
- Few-shot prompts (3 examples from plan §1.2 — still optimal per arXiV research)
- Circuit breaker with `pybreaker` (fail_max=3, timeout=30s, heuristic fallback)
- LRU cache on `(query_hash, goal_hash)` → 256 entries

**Changes from original plan:**
- Add `KINDLY_CLASSIFIER_URL` setting (defaults to Cloud Run URL)
- Add `KINDLY_CLASSIFIER_ENABLED` setting (defaults to `True` when URL set)
- Heuristic fallback must return `RewritePolicy` compatible values — the original plan's `KEYWORD_FALLBACK_RULES` returns a dict but should return structured `ClassifierOutput`
- Circuit breaker: `asyncio.sleep()` pattern from original plan must be replaced with proper `pybreaker.CircuitBreaker` async usage

**Integration point:** `query_policy_resolver.py` currently calls `classify_search_query(query)` which returns `RewritePolicy(mode="bypass"/"expand")`. The classifier would be called in parallel alongside the existing keyword/neural/community LLM rewrite calls in `query_rewrite.py:284-291`.

### 2.2 Budgeted Decomposition Prompts

**Original plan §2 — STATUS: NOT IMPLEMENTED, UPDATED**

**What to add to `query_rewrite_prompts.py`:**
- `DECOMPOSITION_SYSTEM_PROMPT` (plan §2.2)
- `DECOMPOSITION_SCHEMA` as a JSON schema for structured output
- `DECOMPOSITION_FEW_SHOT` examples (plan §2.4)

**Changes from original plan:**
- The original planned `target_provider` values were `"keyword"`, `"neural"`, `"community"` — current codebase already has `QueryTarget = Literal["keyword", "neural", "community", "all"]`. Align with existing enum.
- The LLM call for decomposition should go through the same `query_rewrite_router.py` LiteLLM path (Mistral/Cerebras/Groq pool) rather than a separate endpoint. Current `_request_variants()` already handles the router — create a parallel `_request_decomposition()` function.

### 2.3 Enhanced Weighted RRF

**Original plan §3 — STATUS: NOT IMPLEMENTED, PARTIALLY UPDATED**

**What to add to `merge.py`:**
1. `list_weights: list[float] | None = None` parameter to `merge_search_results()`
2. Multiply `list_weights[list_idx]` into the RRF score formula at line 170-171
3. `_detect_disjoint()` function (plan §3.3)
4. When disjoint detected: truncate to top 50, rely on cross-encoder

**Changes from original plan:**
- The original plan described `list_weights` as `list_weights[list_idx]` — verify this is semantically correct. The orchestrator dispatches one `search_single_query()` per variant; `result_lists` array index maps to variant index. Need to confirm `list_weights` aligns.
- Overlap tracking is already partially implemented (merge.py:139-148 uses `Counter` for URL occurrences and computes overlap rate). The `_detect_disjoint()` function can reuse this existing infrastructure.
- Host-cap diversification (merge.py:56-109) already works correctly — no changes needed.

### 2.4 Orchestrator Changes

**Original plan §4 — STATUS: NOT IMPLEMENTED, UPDATED**

**What to change in `orchestrator.py`:**
1. After `rewrite_search_query()` returns, check `classifier_result.should_decompose`
2. If `true`: call decomposition LLM → get `sub_questions` with `target_provider` + `weight`
3. Dispatch: iterate `sub_questions` + `variants`, building `search_tasks` with `list_weights`
4. Pass `list_weights` to `merge_search_results()`
5. Disjointness check → aggressive truncation if needed
6. Cross-encoder already reranks against original query (line 204-205)

**Changes from original plan:**
- Current `run_web_search()` builds search tasks from `rewrite_plan.variants` (line 158-176). Need to also build from `sub_questions` with `_select_providers_for_target()` — which already exists as `_select_providers_for_variant()` (lines 53-70).
- `_select_providers_for_variant()` already handles `target="keyword"`, `"neural"`, `"community"`, `"all"` — decomposition's `target_provider` maps directly.
- The original plan §4.1 proposed a separate `_select_providers_for_target()` function — not needed, reuse existing.

---

## 3. New Recommendations from MCP Tool Exercise (May 2026)

These are additive insights from testing the server as a client with 25 live tool calls and competitive research.

### 3.1 Tier 1: Quick Wins (add to Phase 2)

| # | Recommendation | Rationale | Source |
|---|---|---|---|
| 1 | Add `instructions=` to `FastMCP()` constructor | Current instructions exist (server.py:146-154) but are routing-focused. Add explicit chain contract: `web_search → get_content → synthesis` | Competitive analysis: Firecrawl/Brave use this effectively |
| 2 | Add tool-level timeouts via `@mcp.tool(timeout=N)` | Replaces global `KINDLY_TOOL_TOTAL_TIMEOUT_SECONDS` with per-tool timeouts. FastMCP 2.9+ native feature | Context7 SDK docs |
| 3 | Strip noise fields from tool responses | `mime_hint`, `source_engines`, `category`, `raw_score`, `fetch_backend`, `content_type`, `diagnostics` — ~30-50% overhead per response | Live test measurements + arXiV paper |
| 4 | Add FastMCP built-in `LoggingMiddleware` and `TimingMiddleware` | Free structured logging + token estimates + latency tracking. Zero custom code | FastMCP 2.9+ |
| 5 | Expand `CHEAP_TOOLS` frozenset in rate limiter | 8 tools currently unclassified (academic_search, youtube_search, etc.) — fall through to cheap bucket silently | Code analysis |
| 6 | Add guidance generators for `gemini_search`, `perplexity_search`, `academic_search`, `youtube_search`, `discover_links` | Only 3/9 tools have DynamicGuidance. The middleware is the server's strongest competitive advantage | Live test + competitive analysis |

### 3.2 Tier 2: Medium Effort (defer to Phase 3)

| # | Recommendation | Rationale |
|---|---|---|
| 7 | Add `outputSchema` to all tools via Pydantic return types | MCP 2025-06-18 spec supports structured output. No competitor has this. FastMCP auto-generates from Pydantic models | SDK docs + competitive analysis |
| 8 | Shorten `web_search` description from 3,907 → ~1,200 chars | 3x longer than competitors. arXiV paper confirms >1,200 chars diminishing returns. Move "Prerequisites"/"Notes" to `docs://workflow` resource | arXiV paper + competitive analysis |
| 9 | Fix session tracking: extract shared `SessionTracker` class | Two identical `_get_session_id()` implementations with unbounded memory growth bugs | Code analysis |
| 10 | Replace `asyncio.sleep()` busy-wait in rate limiter with `asyncio.Event` | Spin-wait blocks event loop unnecessarily | Code analysis |

### 3.3 Tier 3: Strategic (future)

| # | Recommendation | Rationale |
|---|---|---|
| 11 | CSV output option for tabular data (`web_search`, `academic_search`) | 40-60% token reduction vs JSON. No competitor offers this | BigDataBoutique article |
| 12 | Add `PromptsAsTools` transform | Exposes prompts as tools for clients that don't support MCP prompts natively | FastMCP docs + `FastMCP-client-steering-plan.md` |
| 13 | Portal-style structural tool filtering on `on_list_tools` | If search returns empty, hide `web_search` and promote `gemini_search` | portal.one pattern |

---

## 4. Pre-existing LSP Errors Found

During analysis, these type errors were detected in the current codebase (not introduced by changes):

| File | Error | Severity |
|---|---|---|
| `server.py:333-334` | Accessing `settings` on `FastMCP[Any]` — attribute unknown | Warning |
| `server.py:995-1016` | `None` subscriptability on `summary_config` dict access | Error |
| `server.py:1150` | `list[dict]` passed where `list[BatchContentResult]` expected | Error |
| `orchestrator.py:253` | `dict` passed where `SearchResultWindow` Pydantic model expected | Error |
| `gemini_advisory.py:13` | `ToolResult` imported from `fastmcp.server.server` — should import from `fastmcp.tools.base` | Warning |
| `query_guidance.py:15` | Same `ToolResult` import path issue | Warning |

These should be fixed as part of cleanup but are pre-existing.

---

## 5. Files to Create/Modify — Updated

| File | Action | Description |
|---|---|---|
| `search/query_classifier_client.py` | **NEW** | FunctionGemma client with few-shot, circuit breaker, cache |
| `search/query_rewrite_prompts.py` | **MODIFY** | Add `DECOMPOSITION_SYSTEM_PROMPT`, `DECOMPOSITION_SCHEMA`, `DECOMPOSITION_FEW_SHOT` |
| `search/query_rewrite_models.py` | **MODIFY** | Add `SubQuestion`, `ClassifierOutput` models |
| `search/query_rewrite.py` | **MODIFY** | Wire classifier call, conditional decomposition trigger |
| `search/merge.py` | **MODIFY** | Add `list_weights` parameter, `_detect_disjoint()` |
| `search/orchestrator.py` | **MODIFY** | Sub-question dispatch, list_weights propagation, disjointness handling |
| `pyproject.toml` | **MODIFY** | Add `pybreaker` dependency |
| `settings.py` | **MODIFY** | Add `KINDLY_CLASSIFIER_URL`, `KINDLY_CLASSIFIER_ENABLED` |
| `server.py` | **MODIFY** | Add `LoggingMiddleware`, `TimingMiddleware`, tool timeouts |
| `middleware/rate_limits.py` | **MODIFY** | Expand `CHEAP_TOOLS` frozenset |
| `middleware/query_guidance.py` | **MODIFY** | Add generators for gemini_search, perplexity_search, academic_search, youtube_search, discover_links |
| `models.py` | **MODIFY** | Add `PUBLIC_FIELDS` filtering for noise reduction |
| `merge.py` | **MODIFY** | `list_weights` parameter |

---

## 6. Verification

```bash
# Unit tests
pytest tests/test_classifier_client.py -v
pytest tests/test_decomposition.py -v
pytest tests/test_merge_weighted.py -v
pytest tests/test_disjoint_detection.py -v
pytest tests/test_query_rewrite_with_decomposition.py -v

# Existing tests must continue passing
pytest tests/test_server.py tests/test_orchestrator.py tests/test_query_rewrite.py -v

# Lint
ruff check src/
ruff format src/
```

---

## References

- Cormack, Clarke & Büttcher (2009). "Reciprocal Rank Fusion." SIGIR '09
- Tang et al. (2025). "MCP Tool Descriptions Are Smelly!" arXiv 2602.14878
- Perplexica, GPT-Researcher, LlamaIndex SubQuestionQueryEngine
- Haystack, Elasticsearch RRF implementations
- FastMCP SDK docs (gofastmcp.com)
- MCP Specification (modelcontextprotocol.io/specification/draft.md)
