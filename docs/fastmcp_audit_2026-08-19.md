# FastMCP Implementation Audit — web-search-mcp

**Date:** 2026-08-19
**Installed FastMCP:** 3.4.2 (`fastmcp>=3.4.0` in `pyproject.toml`, no upper bound)
**Docs reviewed:** gofastmcp.com (whats-new, context, tools, resources, middleware, progress, pagination, logging, elicitation, sampling, code-mode)
**Reference repos:** PrefectHQ/fastmcp (examples), damionrashford/RivalSearchMCP, mai-yyy/multi-llm-mcp, dialog-tools/reddit-research-mcp

---

## 1. Current State Assessment

The codebase is **already on a modern FastMCP 3.x footing** — far ahead of the average community server. It correctly uses most of the idioms that the docs and reference repos recommend.

### What is done well (confirmed in source)

| Capability | Where | Assessment |
|---|---|---|
| `FastMCP(name, version, lifespan, providers, instructions)` | `server.py:116-158` | ✅ Modern constructor; `version="0.1.8"`, `instructions` methodology, `lifespan=_app_lifespan`, `providers=[analytics_app]` |
| `CurrentContext()` dependency injection | all 11 tools (`search.py`, `content.py`, `youtube.py`, `academic.py`, `sitemap.py`, `ai_search.py`, `code_search/tool.py`, `quick_web_search.py`, `composio_tools.py`) | ✅ Correct modern idiom; `ctx` excluded from schema |
| Progress reporting | `search.py:129,196`, `academic.py:64,135`, `sitemap.py:74`, `ai_search.py:58` | ✅ `ctx.report_progress(progress, total, message)` |
| Client logging | `quick_web_search.py:303,334`, `composio_tools.py:142`, `academic.py:65` | ✅ `ctx.info(...)` |
| Custom middleware | 5 modules in `middleware/` | ✅ Uses `Middleware`/`MiddlewareContext`/`ToolError`/`ToolResult` correctly |
| Transforms | `server.py:205-208` (`PromptsAsTools`, `ResourcesAsTools`), `server.py:441-447` (`RegexSearchTransform`) | ✅ Modern transform surface |
| Tool annotations | `tools/catalog.py` — `ToolAnnotations(title, readOnlyHint, idempotentHint, openWorldHint)` | ✅ |
| Tool `version` + `timeout` | `tools/catalog.py:146-159` | ✅ |
| Resources with `tags` + `annotations` + RFC 6570 query params | `server.py:226-255`, `tools/resources.py` | ✅ `analytics://reports/{report_name}{?days}` |
| Prompts with `version` + `tags` | `server.py:259-276` | ✅ |
| Pydantic response models + union return types | `models.py` | ✅ (but see Gap #1) |
| Lifespan shutdown drain | `tools/_helpers.py:189-218` | ✅ |
| `FastMCPApp` analytics provider | `analytics/app.py` | ✅ |

### Middleware stack (in order added, `server.py:170-200`)

1. `StdoutGuardMiddleware` — redirects stdout→stderr during tool calls (domain-specific, justified)
2. `ArgumentAliasingMiddleware` — rewrites hallucinated params (uses `context.copy(message=...)`, correct 4.x pattern)
3. `ExpensiveToolProtectionMiddleware` — blocks first `grok_search` call, raises `ToolError`
4. `DifferentiatedRateLimitMiddleware` — hand-rolled token bucket
5. `DynamicGuidanceMiddleware` — result-aware guidance, returns `ToolResult`

---

## 2. Gap & Opportunity Analysis

### 🔴 Gap #1 — Error handling returns dicts instead of raising `ToolError` (highest impact)

Every tool catches exceptions and **returns** a dict via `format_tool_error()` (`errors.py:277-305`) with `isError: True` embedded in the payload. This is the single biggest divergence from modern FastMCP.

**Why it matters:**
- From the MCP protocol's perspective the tool **succeeds** — the client never sees a real `isError`/error result, so retry/error UX and `ErrorHandlingMiddleware` can't fire.
- It forces the union return type `WebSearchResponse | ToolErrorResponse` (`models.py:354-361`), which produces a messy output schema and forces `query_guidance.py:41-52` to `_unwrap_fastmcp_result()` and manually sniff errors.
- It duplicates what `ToolError` + `mask_error_details` already provide natively.

**Modern idiom:** raise `ToolError` (or let the exception propagate) and return only the success model.

### 🟠 Gap #2 — Legacy context access (`ctx.fastmcp_context.*`)

`tools/_helpers.py:53-64` and `middleware/session_tracking.py:20-34` reach into `ctx.fastmcp_context.session_id` / `.client_id`. FastMCP 3.4.2 exposes these **directly** on `Context` (`ctx.session_id`, `ctx.client_id`). The `fastmcp_context` attribute is an internal/legacy path that will change in v4.

### 🟠 Gap #3 — Hand-rolled middleware where built-ins exist

The repo re-implements rate limiting (token bucket), and has no logging/timing/error-handling/response-limiting middleware. FastMCP ships production-ready built-ins that RivalSearchMCP demonstrates using:

- `RateLimitingMiddleware` / `SlidingWindowRateLimitingMiddleware`
- `LoggingMiddleware` / `StructuredLoggingMiddleware`
- `TimingMiddleware` / `DetailedTimingMiddleware`
- `ErrorHandlingMiddleware`
- `ResponseLimitingMiddleware`
- `PingMiddleware`
- `ResponseCachingMiddleware`

The custom `StdoutGuardMiddleware` and `ArgumentAliasingMiddleware` are genuinely domain-specific and should stay. The token-bucket rate limiter and the observability-event logging could be replaced/supplemented with built-ins.

### 🟠 Gap #4 — No `mask_error_details=True`

`server.py` does not set `mask_error_details`. Combined with Gap #1 (returning raw `str(exc)` in error dicts), internal exception details leak to clients. RivalSearchMCP sets `mask_error_details=True`.

### 🟡 Gap #5 — Missing structured output schemas on several tools

`gemini_search`, `grok_search`, `generate_sitemap`, `discover_links` return bare `dict` with no return annotation → **no output schema** is generated. `web_search`/`get_content`/etc. have union annotations but the union-with-error anti-pattern (Gap #1) degrades them.

### 🟡 Gap #6 — Underused Context capabilities

- **`ctx.warning` / `ctx.error` / `ctx.debug` / `ctx.log`** — only `ctx.info` is used. Partial provider failures (which the codebase already tracks via `ProviderWarning`) should emit `ctx.warning`.
- **`ctx.set_state` / `ctx.get_state`** — request-scoped state is unused; `session_tracking` recomputes session id per middleware.
- **`ctx.read_resource` / `ctx.get_prompt` / `ctx.list_resources` / `ctx.list_prompts`** — unused; tools could programmatically read `docs://workflow` etc.

### 🟡 Gap #7 — Expensive-tool "block first call" is a workaround for elicitation

`ExpensiveToolProtectionMiddleware` blocks the first `grok_search` call and returns a steering message. This is exactly what **elicitation** (`ctx.elicit()` on handshake-era, or the `InputRequiredResult` guard pattern on modern connections) is designed for — a proper confirmation/refinement round-trip rather than a hard block.

### 🟡 Gap #8 — Long-running tools use foreground `timeout`, not background tasks

`web_search` (60s), `generate_sitemap` (90s), `code_search` (120s) use `timeout=` but run in the foreground. FastMCP 4's `@mcp.tool(task=True)` + `fastmcp-tasks` is the modern answer for long-running work (client polls a task handle instead of holding a request open).

### 🟡 Gap #9 — FastMCP 4 readiness

- `fastmcp>=3.4.0` has **no upper bound** — a `pip install` could pull v4 and break on removed APIs. Pin `<4` until migration is planned.
- v4 removes `ctx.sample()`, `ctx.sample_step()`, `ctx.list_roots()`, and the `sampling_handler=`/`sampling_handler_behavior=` constructor args. This repo does **not** use them (good), but the `ctx.fastmcp_context` legacy access (Gap #2) and the `Context` import path should be verified against v4.
- v4 changes elicitation to the `InputRequiredResult` guard pattern (`ctx.input_responses`, `ctx.request_state`). The `ExpensiveToolProtectionMiddleware` would need rework.

### ⚪ Low priority

- **`list_page_size`** pagination — ~15 tools + 8 resources + 3 prompts is under the ~100 threshold; skip unless the analytics provider grows.
- **`on_duplicate="error"`** — RivalSearchMCP uses it; default is `"warn"`.
- **`website_url`, `icons`** — not set.
- **`Depends()`** — not used for hidden params (not needed here).
- **`get_context()`** — not available in 3.4.2 (v4 feature); the repo correctly threads `ctx` through params instead.

---

## 3. Actionable Recommendations (prioritized)

### P0 — Replace error-dict returns with `ToolError` raises

**Files:** `errors.py`, all 11 tool modules, `models.py`, `query_guidance.py`.

Keep `classify_error()` (it's good classification logic) but change the terminal behavior from "return a dict" to "raise `ToolError`".

```python
# errors.py — add a raise helper alongside the existing classifier
from fastmcp.exceptions import ToolError

def raise_tool_error(exc: Exception, provider: str | None = None) -> None:
    """Classify and raise a FastMCP ToolError (never returns)."""
    structured = classify_error(exc, provider=provider)
    raise ToolError(
        structured.error,
        # structured.action / provider / status_code can ride in the message
        # or be dropped; ToolError already surfaces the message to the client.
    )
```

Then in each tool, replace the `except ... return format_tool_error(...)` tail:

```python
# tools/search.py — before
except Exception as exc:
    ...
    return format_tool_error(exc, provider="searxng")

# tools/search.py — after
except Exception as exc:
    ...
    raise_tool_error(exc, provider="searxng")
```

And drop the union return types so output schemas are clean:

```python
# models.py — before
WebSearchResultType = WebSearchResponse | ToolErrorResponse

# models.py — after (success model only)
# web_search returns WebSearchResponse; failures raise ToolError
```

```python
# tools/search.py — before
async def web_search(...) -> WebSearchResultType:

# tools/search.py — after
async def web_search(...) -> WebSearchResponse:
```

**Impact:** real MCP `isError` semantics, clean output schemas, `ErrorHandlingMiddleware` can fire, `query_guidance.py` loses its `_unwrap_fastmcp_result`/error-sniffing complexity. **Blast radius:** every tool + `models.py` + `query_guidance.py` + tests that assert `isError` in payloads. Do this as one coordinated change with a test sweep.

---

### P1 — Use modern `Context` attributes directly

```python
# tools/_helpers.py — before
def _resolve_session_id(ctx: Context | None) -> str | None:
    if ctx is None:
        return None
    fastmcp_context = getattr(ctx, "fastmcp_context", None)
    if fastmcp_context is not None:
        session_id = getattr(fastmcp_context, "session_id", None)
        if session_id:
            return str(session_id)
        client_id = getattr(fastmcp_context, "client_id", None)
        if client_id:
            return str(client_id)
    return None

# tools/_helpers.py — after
def _resolve_session_id(ctx: Context | None) -> str | None:
    if ctx is None:
        return None
    try:
        return ctx.session_id
    except RuntimeError:
        return ctx.client_id
```

```python
# middleware/session_tracking.py — before
fastmcp_context = context.fastmcp_context
if fastmcp_context is not None:
    try:
        return fastmcp_context.session_id
    except RuntimeError:
        client_id = fastmcp_context.client_id
        if client_id:
            return client_id

# middleware/session_tracking.py — after
fastmcp_context = context.fastmcp_context  # MiddlewareContext still exposes this
if fastmcp_context is not None:
    try:
        return fastmcp_context.session_id
    except RuntimeError:
        return fastmcp_context.client_id or None
```

(Note: `MiddlewareContext.fastmcp_context` is still the correct accessor in middleware; the fix is primarily in `_helpers.py` where a `Context` is already in hand.)

---

### P1 — Add `mask_error_details=True` and built-in middleware

```python
# server.py — constructor
mcp = FastMCP(
    "web-search",
    version="0.1.8",
    lifespan=_app_lifespan,
    providers=[analytics_app],
    mask_error_details=True,          # ← add
    instructions=(...),
)
```

Supplement the custom stack with built-ins (order matters — error handling early, logging late):

```python
# server.py — after the custom middleware, before tool registration
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.timing import TimingMiddleware
from fastmcp.server.middleware.logging import StructuredLoggingMiddleware
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware

mcp.add_middleware(ErrorHandlingMiddleware(include_traceback=False))
mcp.add_middleware(TimingMiddleware())
mcp.add_middleware(StructuredLoggingMiddleware(include_payloads=False))
mcp.add_middleware(
    ResponseLimitingMiddleware(
        max_size=1_000_000,
        tools=["web_search", "batch_get_content", "code_search", "generate_sitemap"],
    )
)
```

Consider replacing `DifferentiatedRateLimitMiddleware` with `SlidingWindowRateLimitingMiddleware` (per-tool-group buckets are a nice-to-have; the built-in is simpler and battle-tested). Keep `StdoutGuardMiddleware` and `ArgumentAliasingMiddleware` as-is.

---

### P2 — Add output schemas to the `dict`-returning tools

```python
# tools/ai_search.py — before
async def gemini_search(...) -> dict:

# tools/ai_search.py — after
async def gemini_search(...) -> GeminiSearchResponse:
    ...
    return GeminiSearchResponse(**result)   # or return the model directly
```

Same for `grok_search` (`-> GrokSearchResponse`), `generate_sitemap` (define a `SitemapResponse` model), `discover_links` (`-> DiscoverLinksResponse`). This gives clients `structuredContent` + output schemas for every tool.

---

### P2 — Use `ctx.warning` for partial provider failures

```python
# tools/search.py — where provider warnings are collected
if response.warnings:
    for w in response.warnings:
        await ctx.warning(f"Provider {w.provider}: {w.message}")
```

And `ctx.debug` for stage transitions in the 7-stage content fetcher / rerank funnel.

---

### P3 — Replace the expensive-tool block with elicitation (or the v4 guard pattern)

On FastMCP 3.4.2 (handshake-era), `ctx.elicit()` is available:

```python
# tools/ai_search.py — grok_search, instead of middleware block
async def grok_search(query: str, ..., ctx: Context = CurrentContext()) -> GrokSearchResponse:
    confirmation = await ctx.elicit(
        "grok_search is rate-limited and costly. Confirm you've refined the query.",
        response_type=bool,
    )
    if confirmation.action != "accept" or not confirmation.data:
        raise ToolError("grok_search cancelled — refine the query and retry.")
    ...
```

This is a cleaner round-trip than the "block first call" middleware. **Caveat:** on FastMCP 4 modern connections `ctx.elicit()` raises an era error; the v4 pattern is to return `InputRequiredResult` and read `ctx.input_responses`. If you migrate to v4, rework this into the guard pattern.

---

### P3 — Pin FastMCP and plan the v4 migration

```toml
# pyproject.toml
dependencies = [
    "fastmcp>=3.4.0,<4",   # ← add upper bound until v4 migration is done
    ...
]
```

Migration checklist (from the whats-new doc):
- Remove any `ctx.sample()` / `ctx.sample_step()` / `ctx.list_roots()` (none present — ✅).
- Remove `sampling_handler=` / `sampling_handler_behavior=` (none present — ✅).
- Replace `ctx.fastmcp_context` legacy access (Gap #2).
- Rework elicitation to `InputRequiredResult` guard pattern (Gap #7).
- Consider `@mcp.tool(task=True)` + `fastmcp-tasks` for `web_search`/`sitemap`/`code_search` (Gap #8).

---

## Summary of recommendations

| # | Priority | Recommendation | Effort |
|---|---|---|---|
| 1 | P0 | Raise `ToolError` instead of returning error dicts; drop union return types | Large (all tools + tests) |
| 2 | P1 | Use `ctx.session_id`/`ctx.client_id` directly | Small |
| 3 | P1 | Add `mask_error_details=True` + built-in middleware (error/timing/logging/response-limit) | Small |
| 4 | P2 | Add output schemas to `dict`-returning tools | Medium |
| 5 | P2 | Use `ctx.warning`/`ctx.debug` for partial failures & stage transitions | Small |
| 6 | P3 | Replace expensive-tool block with elicitation/guard pattern | Medium |
| 7 | P3 | Pin `fastmcp<4`; plan v4 migration (tasks, guard pattern) | Small to start |

---

## Implementation status (2026-08-19)

| # | Status | Notes |
|---|---|---|
| 1 | ✅ Done | `errors.raise_tool_error()` classifies + raises `ToolError` with `.structured` attached; all tools migrated (`academic_search`, `grok_search`, `generate_sitemap`, `youtube_transcript`/`youtube_search`, `quick_web_search`, `composio_similarlinks`, `gemini_search`). Union aliases and `ToolErrorResponse` removed from `models.py`; tools declare single-model output schemas. Model drift fixed (`GetContentResponse.url/cached/origin_backend`, `GeminiSearchResponse`, `GrokSearchResponse`, `BatchContentResult.page_char_count/word_count`, new `SitemapResponse`). |
| 2 | ✅ Done | `_resolve_session_id` uses `ctx.session_id`/`ctx.client_id` with `get_context()` fallback. |
| 3 | ✅ Done (deviation) | `mask_error_details=True`, `client_log_level="WARNING"`, `TimingMiddleware`, `StructuredLoggingMiddleware(include_payloads=False)`, `ResponseLimitingMiddleware(max_size=1MB)`, `ResponseCachingMiddleware` (read_resource TTL 300s; call_tool + list_tools caching disabled — list_tools caching drops `task_config` and breaks SEP-1686 advertisement). **`ErrorHandlingMiddleware` intentionally NOT added**: it rewrites `ToolError` messages to `"Internal error: ..."`, degrading the actionable messages raised per #1. |
| 4 | ✅ Done | All dict-returning tools now have single-model output schemas (see #1). |
| 5 | ✅ Done (partial) | `ctx.warning` emitted for partial provider failures in `web_search`. `ctx.debug` stage transitions deferred (low value). |
| 6 | ⏳ Deferred | Elicitation/guard pattern remains a v4-migration item. |
| 7 | ✅ Done | `fastmcp>=3.4.0,<4` pinned; upgraded to 3.4.7 (fixes `ResponseCachingMiddleware` keyword-arg bug in 3.4.0). `RetryMiddleware` and `FileTreeStore` are v4-only — deferred. |

Additional items implemented beyond the original table:
- **Background tasks (SEP-1686)**: `web_search`, `generate_sitemap`, `code_search`, and `deep_research` are catalog-driven `task=TaskConfig(mode="optional")`; wire format advertises `execution.taskSupport="optional"`.
- **`deep_research` profile move**: `{"full"}` → `{"regular", "full"}`.
- **Cache stats resource**: `cache://stats` + `cache://stats/{cache_name}` template with `entry_count()` on all cache facades.
- **BM25 search transform**: `RegexSearchTransform` → `BM25SearchTransform` (natural-language `query` param).
