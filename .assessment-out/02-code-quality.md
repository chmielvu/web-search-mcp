# Code Quality & Patterns Review — web-search-mcp

Scope: 305 files under `src/kindly_web_search_mcp_server/`. Citations use `path:line`. Deltas only — no rewrites.

---

## 1. Top 10 largest Python files

| # | File | Lines | Public surface | One-liner |
|---|------|------:|----------------|-----------|
| 1 | `settings.py` | 591 | `Settings` (dataclass, ~70 fields) | Env-first runtime config; `__post_init__` re-reads `os.environ`, raises `ValueError` on malformed JSON. |
| 2 | `content/summary_backend.py` | 545 | `summarize_with_fallback`, `summarize_batch_with_fallback` | Gemini summarization; fallback tier; `asyncio.to_thread` for blocking genai SDK. |
| 3 | `search/gemini_search_tool.py` | 495 | `GeminiGroundingResult`, `gemini_search_with_grounding`, `gemini_search_with_grounding_dual` | Dual-prompt Google-Search grounding via `asyncio.gather`; 3-tier model fallback. |
| 4 | `tools/content.py` | 464 | `get_content`, `batch_get_content`, `discover_links` | MCP entrypoints: cache → 7-stage fetch → window → optional summary → store. |
| 5 | `analytics/writers/schema.py` | 441 | `ensure_store_schema`, `ensure_search_quality_tables`, `ensure_vss_extension` | DuckDB bootstrap for 12 fact tables + HNSW extension. |
| 6 | `content/resolvers/github_discussions.py` | 428 | `parse_github_discussion_url`, `GitHubGraphqlClient`, `render_discussion_thread_markdown` | GraphQL fetch + 150-line markdown formatter for GitHub discussions. |
| 7 | `cli/commands/experiments.py` | 415 | 8 typer sub-commands | A/B config CRUD; each wraps `_load_experiments` in `CliError(kind="not_found"\|"tool_error")`. |
| 8 | `analytics/views.py` | 412 | module-level bootstrap | 10 dashboard SQL `CREATE OR REPLACE VIEW` builders, hand-written CASE/COALESCE. |
| 9 | `search/providers/grok.py` | 388 | `GrokProviderError`, `search_grok_openrouter`, `grok_search` | OpenRouter/Grok 4.3 with native web + x_search; nested closures. |
| 10 | `search/academic/academic_search_orchestrator.py` | 386 | `search_academic` (async) | Parallel arxiv/S2/OpenAlex/CrossRef/PubMed/CORE → dedup → sort. |

## 2. Complexity hotspots (rough cyclomatic)

1. `plan_search` — `search/planning.py:203-356` (~150 lines, ~25 branches). Six `QueryBranch` literals each gated by 3-condition ternaries (`if request.rewrite and dc.rewrite_metadata and "error" not in dc.rewrite_metadata` repeated 5×); nested try/except around the LLM rewrite falls back to a 5-tuple.
2. `retrieve_branches` — `search/retrieval.py:182-340` (~160 lines, ~20 branches). Manual `asyncio.wait` + `done`/`pending` partition, then 4 nearly-identical `_record_provider_result(...)` calls (`pending` / `done` / `CancelledError` / `Exception`).
3. `gemini_search_with_grounding_dual` — `search/gemini_search_tool.py:528-end` (~80 lines, ~15 branches). Outer `for model in tier` × inner `try/except` × per-model retry-with-1s-backoff; `_try_model` raises on every error.
4. `render_discussion_thread_markdown` — `content/resolvers/github_discussions.py:62-end` (~150 lines, ~30+ branches). Linear stream of `isinstance(x, dict)` guards and per-comment formatting.
5. `gemini_search_with_grounding` + `_call_single_grounding` — `search/gemini_search_tool.py:366-403` + `505-526`. Tier loop with `should_retry` × `should_fallback` double-conditional and `for/else`-style `continue` ladder; 12-field result.

## 3. Async hygiene

- `youtube/yt_dlp_backend.py:130` — `httpx.get(url, ...)` (sync) inside `_parse_json3`, reachable from `fetch_whisper_transcript`'s async path. Blocking sync I/O on the event loop, no `to_thread` wrapper.
- `cli/runtime.py:156` — `asyncio.run(_marked_runner())` at the top of `lifecycle()`. Not inside a running loop today, but any future caller that invokes `lifecycle()` from an MCP handler would deadlock. Worth a guard.
- `telemetry/_internal.py:234` — sync `httpx.Client` inside `_probe_otlp_endpoint`. Safe today (sync startup path) but re-exported via async `__init__.py`; call-site reviewers must remember the contract.
- `embeddings/hf_inference.py:304` — `asyncio.sleep(retry_delay)` carries `# type: ignore[name-defined]`. `retry_delay` is defined at line 265, so the suppression is stale.
- `analytics/motherduck_sync.py:286` — `time.sleep` in a `while True` loop. The function is sync, so correct; flagged because grep finds it adjacent to async modules.

No `asyncio.run` is currently invoked from a running loop. `_probe_otlp_endpoint`'s sync-client pattern is the highest-risk if a caller migrates it.

## 4. Error handling consistency

Sampled modules:

- `cli/commands/experiments.py` ✅ — every command raises `CliError(kind=..., exit_code=ExitCode.<x>)` (10 occurrences), funneled via `app.py:140` → `raise SystemExit(int(exc.exit_code))`.
- `cli/services/content_batch.py:80` ❌ — `except Exception: pass` silently swallows the per-item `get_page_cache().astore` failure.
- `analytics/app_queries.py:56,65,248` ⚠️ — three `except Exception: return []` (or `default`). `_query`'s docstring calls it a "safe fallback" but never logs.
- `search/providers/grok.py` ✅ — typed errors (`GrokProviderError`, `GrokProviderConfigError`) at boundaries; `_do_request` wraps `resp.json` and re-raises.
- `cache/page_cache.py` ⚠️ — five `except Exception as exc: LOGGER.warning(...)` blocks. Consistent, but "any failure → warn and proceed" silently degrades a corrupted cache to misses.

`CliError` / `ExitCode` envelope is consistent *inside* `cli/commands/`, but `cli/services/` is mixed: `content.py` raises typed errors while `content_batch.py` swallows them. `tools/content.py:60` etc. catch `Exception` and rewrap into the tool's response — that path correctly does *not* go through `ExitCode` (MCP layer doesn't use them), but the asymmetry is worth flagging.

## 5. Logging hygiene

- `server.py:265` — only `print(` in non-`cli` production code. Intentionally `stderr` and pre-`mcp.run`, so correct.
- `search/gemini_search_tool.py:386-462` — `logger.warning("...%s...", model_id, exc, ...)` interpolates the raw `exc`. For Gemini 4xx, the stringified exception can include the request body — should be `type(exc).__name__` + redacted message.
- `utils/observability.py:321` — `logger.log(level, json.dumps(payload, ...))` serializes the full normalized tool payload. `page_content`/`answer` are redacted via `preview_text` (good), but `metadata` is verbatim and may carry user-typed snippets.
- Inconsistent log levels: `cache/page_cache.py:51` uses `logger.warning` for *lookup* failures, while `:160` uses `logger.debug` for the same condition. Pick one.

## 6. Type-hint coverage

A quick scan (regex over `def` signatures with `->`):

- `composio_tools.py` — 7 defs, 0 with return type. The `@mcp.tool` wrappers at `:164` and `:177` are annotated `-> dict:` — too loose; should be `-> dict[str, Any]`. Helpers lack return types.
- `cli/app.py:31` — `global_options` (typer callback) and `main` lack `-> None` annotations.
- `analytics/app_queries.py` — 6 defs, 0 with return types. `_query` returns `list[dict[str, Any]]` per its docstring but the signature is implicit.

`settings.py`, `errors.py`, and the content/tool files have full coverage. Coverage correlates with "newer refactor phase" vs "legacy free-form code".

## 7. Dead / unused code

- `contracts/base.py:16-17` — `pydantic.GetCoreSchemaHandler` and `pydantic_core.{CoreSchema,core_schema}` imported, never used. Ruff F401, auto-fixable.
- `analytics/writers/schema.py:12` — `from typing import TYPE_CHECKING` imported but the `if TYPE_CHECKING:` block at line 32 references no names.
- `embeddings/hf_inference.py:304` — `# type: ignore[name-defined]` on `retry_delay`; the variable is bound at line 265. Stale suppression.
- `cli/services/content_batch.py:80-81` — `except Exception: pass` after a successful store; the only `pass` in a non-empty handler in the file. Both swallowing and the dead `pass` should be replaced with a single `LOGGER.debug`.

## 8. Naming nits

1. `search/providers/grok.py:191` `search_grok_openrouter` vs `:334` `grok_search` — both call OpenRouter; the first returns `list[WebSearchResult]`, the second returns `GrokSearchResult` (`:312`). Names suggest a thin alias; they are not.
2. `content/summary_backend.py` `_get_client` (~`:95`) vs `_get_batch_client` (~`:155`) — both return a `genai.Client`; only difference is the API key. `_batch_` prefix is a misnomer because the helper is also used by the per-item fallback.
3. `search/planning.py:230` — `_rewrite_queries` invokes an LLM and mutates `dc.rewrite_metadata` via its return tuple's second element. A `get_`/`fetch_` name would be honest about the side-effect.
4. `cli/services/content.py:36` `_cached_artifact` vs `:73` `_artifact_from_fetch_exception` — same output contract, different `source_type` (`"cache"` vs `"unknown"`) and key order.

## 9. Refactor candidates (concrete, scoped)

1. **`plan_search` → branch-table builder** — `search/planning.py:203-356`. Replace the six `QueryBranch(...)` literals with a `tuple[tuple[BranchRole, _CandidateKeys, _Index], ...]` table plus a comprehension. The 6 × 3-condition ternaries for `why=` differ only in role name. ~150 → ~70 lines; trivial to add a 7th branch.
2. **Six-arity `_record_provider_result` → strategy map** — `search/retrieval.py:182-340`. The 4 nearly-identical 9-kwarg call sites differ only in `status_override` / `error_message_override`. Extract an `OutcomeKind` enum (`incomplete | cancelled | errored | ok`) and a single `_record(outcome_kind, ...)`. ~40 fewer lines.
3. **Swallow-and-pass in batch cache store** — `cli/services/content_batch.py:78-83`. Replace `except Exception: pass` with `LOGGER.debug("batch cache store failed for %s: %s", canonical_url, exc)`. Zero risk, one-line change.

## 10. ruff score

```
$ ruff check src/kindly_web_search_mcp_server/ --statistics
3  F401  [*] unused-import
2  W292  [*] missing-newline-at-end-of-file
Found 5 errors.
[*] 5 fixable with the `--fix` option.
```

Only 5 lint findings across 305 files, all auto-fixable. F401 is concentrated in `contracts/base.py:16-17` (3 imports) and `analytics/writers/schema.py:12` (1 dead `TYPE_CHECKING`); W292 are trailing-newline misses.

---

CODE QUALITY REVIEW DONE — Project is lint-clean with a consistent `CliError`/`ExitCode` envelope in CLI commands, but has 5 high-complexity functions (`plan_search`, `retrieve_branches`, dual-grounding tier loop, discussion-thread renderer, retrieval bookkeeping) each warranting one focused refactor, plus 3 stale type-ignore / unused-import suppressions and one silent cache-store `except: pass` worth deleting.
