# Code Cleanup — Final Reconciliation Plan

> **Status:** Updated 2026-05-29 — reconciled against current codebase state  
> **Based on:** `code-cleanup-proposal.md` (original plan) + current `src/` analysis

---

## 1. What Changed Since the Original Cleanup Plan

The original `code-cleanup-proposal.md` was written before several key changes landed:

| Original Assumption | Current Reality |
|---|---|
| `query_policy_hf.py` is dead code to delete | **Still exists.** Query routing simplified to `query_policy.py` with `RewriteMode = Literal["bypass", "expand"]` — two-state logic, no HF backend. `query_policy_resolver.py` explicitly documents this. |
| `gemini_search_tool.py` uses `google.genai`, should be refactored to Pollinations | **Still exists.** `gemini_pollinations.py` exists as a provider but `gemini_search_tool.py` still imports `google.genai` for the standalone `gemini_search` tool. |
| Dual Gemini backends is a code smell | **Slightly changed.** The Pollinations backend fires via `gemini_pollinations.py` in the provider mix. The standalone tool still uses the Google SDK. The original recommendation still holds. |

---

## 2. Dead Code: Confirmed & Updated

### 2.1 `search/query_policy_hf.py` — DELETE

**Status:** Exists at `src/kindly_web_search_mcp_server/search/query_policy_hf.py`  
**Why dead:** `query_policy_resolver.py:1-4` states: *"No HF Space backend — simplified to direct precision signal detection."* The `classify_search_query()` function in `query_policy.py` is purely heuristic with 16 compiled regex patterns. The HF path is unreachable.

**Action:**
1. Delete `search/query_policy_hf.py`
2. Remove `KINDLY_HF_QUERY_CLASSIFIER_URL` from `settings.py` if present
3. Check `tests/` for imports of `query_policy_hf` and remove/update

### 2.2 `search/gemini_search_tool.py` — REFACTOR THEN DELETE

**Status:** Exists at `src/kindly_web_search_mcp_server/search/gemini_search_tool.py`  
**Why redundant:** `gemini_pollinations.py` provides the same Gemini+grounding capability via the free Pollinations API. The tool uses `google.genai` SDK which adds a heavy dependency.

**Prerequisite check:** Verify `gemini_pollinations.py` supports `structured_output=True` (the `gemini_search` MCP tool parameter). If not, add it first.

**Action:**
1. Add `structured_output` support to `gemini_pollinations.py` if missing
2. Rewire `gemini_search()` in `server.py` to use the Pollinations backend
3. Delete `search/gemini_search_tool.py`
4. Remove `google-genai` from `pyproject.toml` dependencies

### 2.3 `query_policy_hf.py` references in tests

**Check and remove any test imports or mocks referencing `query_policy_hf`.**

---

## 3. Consolidation: Still Valid, De-scoped

### 3.1 `server.py` God Module (~85KB, ~2,400 lines)

**Status:** Still valid concern. The file has grown with `discover_links`, `academic_search`, `youtube_search`, and `youtube_transcript` inline implementations.

**Recommendation:** Extract tool handlers into a `tools/` package:
```
tools/
  web_search.py
  content.py       # get_content, batch_get_content
  discover.py      # discover_links
  ai_search.py     # gemini_search, perplexity_search
  academic.py      # academic_search
  youtube.py       # youtube_search, youtube_transcript
```
`server.py` becomes: FastMCP init + middleware wiring + tool registration only.

**Not urgent** — the original cleanup plan already flagged this. Defer to phase 2.

### 3.2 `telemetry.py` (~67KB, ~2,120 lines)

**Status:** Still valid. OpenTelemetry constants, singletons, wrappers crammed into one file.

**Recommendation:** Split into `telemetry/` package: `constants.py`, `metrics.py`, `traces.py`, `events.py`.

**Not urgent** — defer to phase 2.

---

## 4. Duplicated Code: Still Valid

### 4.1 HTTP Client & Retry Logic in Search Providers

**Status:** `tavily.py`, `brave.py`, `jina.py` still duplicate `httpx.AsyncClient` init, timeout handling, and retry logic.

**Recommendation:** Create `search/network.py` with `execute_http_request()` utility. Each provider only defines URLs, payloads, headers, and response parsing.

### 4.2 Academic Search Providers

**Status:** 6 academic providers (`academic_s2.py`, `academic_arxiv.py`, `academic_openalex.py`, `academic_crossref.py`, `academic_pubmed.py`, `academic_core.py`) duplicate pagination handling and JSON mapping.

**Recommendation:** Extract shared base class into `search/academic_core.py`. Already partially done — consolidate further.

### 4.3 Session Tracking (NEW FINDING)

**Status:** Not in original plan. `expensive_tool_protection.py:91-112` and `gemini_advisory.py:93-105` have **identical** `_get_session_id()` implementations with the same fragile fallback chain.

**Recommendation:** Extract into a shared `SessionTracker` utility class with TTL expiry.

---

## 5. New Cleanup Opportunities Found

### 5.1 Remove `GeminiAdvisoryMiddleware`

**Finding:** The advisory message (`gemini_advisory.py:21-23`) is 2 sentences already covered by the `gemini_search` tool description. The session tracking duplicates `ExpensiveToolProtectionMiddleware`. The `_call_counts` dict never expires.

**Recommendation:** Delete `gemini_advisory.py` and merge its function into `DynamicGuidanceMiddleware` as a `"gemini_search"` guidance generator that reads actual grounding data.

### 5.2 Noise Fields in Responses (from live test findings)

**Finding:** `WebSearchResult` fields `mime_hint`, `source_engines`, `category`, `raw_score`, `diagnostics` and `GetContentResponse` fields `fetch_backend`, `content_type`, `diagnostics` are noise for agents.

**Recommendation:** Add `PUBLIC_FIELDS` filtering per model. Replace `model_dump(exclude_none=True)` with `model_dump(include=PUBLIC_FIELDS)`.

---

## 6. Updated Implementation Order

| Priority | Task | Effort | Dependency |
|---|---|---|---|
| 1 | Delete `query_policy_hf.py` | Small | None |
| 2 | Extract shared `SessionTracker` | Small | None |
| 3 | Delete `GeminiAdvisoryMiddleware` (merge into DynamicGuidance) | Medium | None |
| 4 | Refactor + delete `gemini_search_tool.py` | Medium | Add `structured_output` to Pollinations |
| 5 | Noise field filtering | Medium | None |
| 6 | HTTP client dedup (`search/network.py`) | Medium | None |
| 7 | Academic provider base class consolidation | Medium | None |
| 8 | God module split (`tools/` package) | Large | Defer to phase 2 |
| 9 | Telemetry split | Large | Defer to phase 2 |
