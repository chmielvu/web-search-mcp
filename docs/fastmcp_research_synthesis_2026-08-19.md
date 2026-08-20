# FastMCP Research Synthesis — v3 Docs Reassessment (FINAL)

**Date:** 2026-08-19
**Repo:** web-search-mcp (branch main), installed FastMCP **3.4.2**
**Method:** FastMCP docs MCP server (`https://gofastmcp.com/mcp` — `search_fast_mcp` + `query_docs_filesystem_fast_mcp`), llms.txt + sitemap.xml inventory, runtime introspection of installed 3.4.2, `gh` CLI, reference repos.

---

## ⚠️ Corrections to the earlier synthesis (verified against /v3 docs + installed runtime)

1. **Background tasks ARE available in FastMCP 3.x.** The v3 docs (`/v3/servers/tasks.mdx`) document SEP-1686 background tasks (the 2025-11-25 handshake-era task protocol) with `<VersionBadge version="3.0.0" />` for the `tasks` extra. `pip install "fastmcp[tasks]"` on 3.4.2 resolves `pydocket>=0.20.0` (verified via `pip --dry-run`). The installed `pydocket 0.18.2` is too old — that's why `is_docket_available()` returns `False` and `task=True` would currently raise `ImportError` with install instructions. **This is a P1 actionable item, not a v4-migration item.**
2. **`get_context()` IS available in 3.4.2** — from `fastmcp.server.dependencies` (verified importable). The v3 context docs badge it at 2.2.11. (Earlier I checked the wrong module paths.)
3. **Per-session visibility IS available in 3.4.2** — `ctx.enable_components()` / `ctx.disable_components()` / `ctx.reset_visibility()` (verified via `hasattr(Context, ...)`). Earlier I checked `FastMCP` (server-level) and found them missing; they live on `Context`.

---

## 1. FastMCP 4 — availability & what it brings

**Unchanged from earlier synthesis, now with the v3 baseline confirmed:**

- v4 is **beta** (`v4.0.0b3`, 2026-08-14); latest stable is **3.4.7** (2026-08-10). Repo is on 3.4.2.
- v4 adds: sessionless 2026-07-28 protocol + dual-era negotiation, `UserSession`/`SessionId`, `TasksExtension` (replaces SEP-1686 tasks), `InputRequiredResult` guard pattern, server extensions, argument completion, enterprise identity.
- v4 **removes**: `ctx.sample()`/`ctx.sample_step()`/`ctx.list_roots()`, `sampling_handler=` args, and `ctx.elicit()` on modern connections.
- **v3 features the repo can use today** (all verified in 3.4.2): `ctx.elicit()` (2.10.0), `ctx.sample()` (2.0.0), session state `ctx.set_state/get_state/delete_state` (3.0.0, 1-day expiry, `serializable=False` for non-serializable), per-session visibility, `get_context()`, `Depends()`, `CurrentAccessToken()`, `get_http_request()/get_http_headers()/get_access_token()`, `client_log_level` (3.2.0), `list_page_size` (3.0.0), `mask_error_details` (defaults to `FASTMCP_MASK_ERROR_DETAILS` env var), `tasks=True` server-wide default.
- **Security patches the repo is missing on 3.4.2:** 3.4.5 (JWKS Ed25519 key rejection fix), 3.4.6 (trusted SSRF proxy backport), 3.4.7 (CIMD audience fix). Bumping to 3.4.7 is a security-motivated no-brainer.

**Bottom line:** Stay on 3.x; pin `fastmcp>=3.4.0,<4` and bump to **3.4.7**. v4 remains a later, planned migration.

## 2. Long-running / backgrounded processes (~5-minute research tasks)

**REVISED — this is now actionable on 3.4.2:**

- **3.x path (SEP-1686):** `pip install "fastmcp[tasks]"` (upgrades pydocket to ≥0.20.0), then:

```python
from fastmcp import FastMCP
from fastmcp.server.tasks import TaskConfig
from fastmcp.dependencies import Progress

@mcp.tool(task=TaskConfig(mode="optional", poll_interval=timedelta(seconds=5)))
async def deep_research(query: str, progress: Progress = Progress()) -> dict:
    await progress.set_total(6)
    for stage in stages:
        ...
        await progress.increment()
        await progress.set_message(stage)
    return result
```

- `mode="optional"` = sync for legacy clients, background for task-capable clients; `"required"` = background only; `"forbidden"` = never.
- Backends: `FASTMCP_DOCKET_URL=memory://` (default, single-process, ~250ms pickup) or `redis://` (persistent, scalable). Extra workers: `fastmcp tasks worker server.py` (Redis only).
- In 3.x, `task=True` works on tools, resources, resource templates, **and prompts** (v4 restricts to tools).
- Server-wide default: `FastMCP(..., tasks=True)`.
- **v4 path (later):** `TasksExtension` + `fastmcp-tasks` package, negotiated over 2026-07-28 connections.

**Bottom line:** For ~5-minute research tasks, adopt `task=TaskConfig(mode="optional")` + `Progress` on `web_search`/`generate_sitemap`/`code_search` **now** (after `pip install "fastmcp[tasks]"`). This is the single biggest upgrade unlocked by the v3 docs study.

## 3. Prior fetches/searches as resources with TTL

**Confirmed and refined:**

- Repo already has TTL'd caches (page 7d SQLite, query 1d LRU, transcript) + `get_search_history_resource`.
- **Protocol-level TTL:** `ResponseCachingMiddleware` with per-operation settings (`ReadResourceSettings(ttl=...)`, `CallToolSettings(included_tools=[...])`) — verified in 3.4.2 and documented in v3 middleware docs.
- **Persistent cache storage:** `FileTreeStore` (py-key-value-aio) for cross-restart caching; Redis for distributed. Sanitization strategies required for FileTreeStore.
- **Session state** (`ctx.set_state`) is another 3.x mechanism for cross-request reuse within a session (1-day expiry).
- Caveat (from docs): cache keys = operation + arguments only, **not** user/session identity.

**Bottom line:** Add resource templates (`content://{url}`, `search://{query}`) over existing caches + `ResponseCachingMiddleware` with `ReadResourceSettings(ttl=...)` and a `FileTreeStore` backend for persistence.

## 4. `gh` CLI findings — patterns worth adopting

**Unchanged, now confirmed against v3 docs:**

- **RivalSearchMCP** composes built-in middleware (ErrorHandling, SlidingWindowRateLimiting, ResponseCaching, Timing, Logging, ResponseLimiting, Ping) — all of which the v3 docs confirm exist in 3.4.2.
- Official `examples/` (elicitation, task_elicitation, persistent_state, tasks) are canonical.
- **New from v3 docs:** `BM25SearchTransform` (3.1.0) is a natural-language upgrade over the repo's current `RegexSearchTransform`; `RetryMiddleware` (exponential backoff) is available for transient provider failures.

## 5. Tool outputs & descriptions best practices

**Confirmed from v3 tools.mdx:**

- Docstring parsing (Google/NumPy/Sphinx) for descriptions + per-parameter docs; `Annotated`/`Field` takes precedence.
- Structured output rules: object-like returns (dict/Pydantic/dataclass) → always `structuredContent`; primitives need return annotations (wrapped in `{"result": ...}`).
- `output_schema` (2.10.0) must be object-type.
- Errors: raise `ToolError`; `mask_error_details=True` (or `FASTMCP_MASK_ERROR_DETAILS` env var).
- `ResponseLimitingMiddleware` caveat: truncated responses break `output_schema` conformance.
- `client_log_level` (3.2.0) sets the default minimum level for client-bound log messages.

---

## FINAL prioritized adoption path (reassessed)

| # | Priority | Action | Effort | Notes |
|---|---|---|---|---|
| 1 | **P0** | Pin `fastmcp>=3.4.0,<4`; bump to **3.4.7** | Trivial | Security fixes (JWKS, SSRF proxy, CIMD) |
| 2 | **P0** | Raise `ToolError` instead of returning error dicts; drop union return types | Large | Unchanged from prior audit |
| 3 | **P1** | **Adopt background tasks**: `pip install "fastmcp[tasks]"` + `task=TaskConfig(mode="optional")` + `Progress` on `web_search`/`generate_sitemap`/`code_search` | Medium | **New — available on 3.4.2 (SEP-1686)** |
| 4 | **P1** | `mask_error_details=True` + built-in middleware (ErrorHandling, Timing, Logging, ResponseLimiting, ResponseCaching) | Small | Confirmed in 3.4.2 |
| 5 | **P1** | `output_schema`/return annotations on `dict`-returning tools | Medium | Confirmed |
| 6 | **P2** | Resource templates over caches + `ResponseCachingMiddleware` TTL + `FileTreeStore` persistence | Medium | Confirmed |
| 7 | **P2** | `ctx.warning`/`ctx.debug`; `get_context()` for deep call chains; `client_log_level` | Small | `get_context()` verified in 3.4.2 |
| 8 | **P2** | Consider `BM25SearchTransform` over `RegexSearchTransform`; `RetryMiddleware` for transient provider errors | Small | v3 docs |
| 9 | **P3** | Plan v4 migration (TasksExtension, InputRequiredResult guard, UserSession) | Large | v4 stable |

**Key change from the earlier synthesis:** background tasks moved from "v4-only, defer" to **P1, do it now on 3.4.2** via the SEP-1686 `tasks` extra. Everything else is confirmed with the v3 docs as the authoritative baseline.
