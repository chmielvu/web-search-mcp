# web-search-mcp — Codebase Assessment & CLI Exercise Synthesis

**Date:** 2026-07-18 22:36 UTC+2
**Scope:** full source (305 Python files under `src/kindly_web_search_mcp_server/` + `src/classifier_service/`) + 24-command native Typer CLI
**Method:** 3 parallel source audits (architecture, code quality, ~~security~~) + end-to-end CLI exercise of the web-search-cli skill
**Working dir:** `C:\Users\Jan\Documents\GitHub\1Agents1\.CLI\web-search-mcp`
**Reports:** `01-architecture.md`, `02-code-quality.md`, `04-tests.md` (in progress)
**CLI exercise log:** `cli-*.json` files in this directory

> Per user direction, the security/observability source audit is omitted. Findings from CLI exercise that touch on stdout hygiene or secret-shaped behavior are still included (they're bugs in the running app, not a security review of source).

---

## TL;DR — Top 10 things to fix

| # | Severity | Area | Finding |
|---|---------|------|---------|
| 1 | 🔴 Critical | CLI/Output | **OpenTelemetry/Phoenix banner leaks to stdout on every CLI invocation.** The `register(...)` call at module import prints a multi-line banner (`Phoenix Project: web-search-mcp`, `Span Processor: BatchSpanProcessor`, `Collector Endpoint: …`, `Transport: HTTP + protobuf`, `Transport Headers: {'authorization': '****'}`) BEFORE the JSON envelope. Breaks the JSON contract. Sometimes leaks AFTER the JSON on early errors. The `****` redaction is better than nothing, but the structure exposes the auth header. |
| 2 | 🔴 Critical | CLI/Output | **`content get` returns `page_content` as a Python `repr()` dict** (single quotes, `None` instead of `null`). Example: `"page_content": "{'url': '…', 'filter': 'fit', 'query': None, …}"`. Any client doing `json.loads(stdout)` gets a `JSONDecodeError` for the entire envelope because the field is unquoted-string. |
| 3 | 🔴 Critical | Docs | **`SKILL.md` documents `--num-results` on `search web` — it doesn't exist.** Schema output: only `query`, `rewrite`, `research_goal`, `searxng_*`, `site_filter`, `domain_filter`, `domain_boost`, `domain_block`, `diagnostics`. Calling it raises a `usage_error` and exit code 2. |
| 4 | 🔴 Critical | Docs | **`SKILL.md` example uses `analytics report --report-name "provider_health"`** — the report doesn't exist. Real reports: `candidate-survival`, `error-taxonomy`, `eval-quality-summary`, `latency-breakdown`, `provider-final-contribution`, `provider-performance`, `rewrite-effectiveness`. |
| 5 | 🟠 High | Analytics/SQL | **`analytics report latency-breakdown` is broken with a SQL binder error:** `Binder Error: Could not ORDER BY column "CASE WHEN ((stage = 'total')) THEN (1) …": add the expression/function to every SELECT, or move the UNION into a FROM clause.` Reproducible. The `rewrite-effectiveness` report has a related smell: a row with `rewrite_enabled: true, rewrite_model: "none"` shows `rewrite_error_rate_pct: 80.0` — looks like a join key bug. |
| 6 | 🟠 High | CLI/Dependency | **`content batch` requires `firecrawl` module** even when `content get` works with just `crawl4ai`. The error message says "Run `web-search-cli doctor` and verify fetch dependencies" — but `doctor` doesn't actually check for `firecrawl`. Error envelope also breaks the JSON contract (no `schema_version`/`data` block, just an `error` object). |
| 7 | 🟠 High | Architecture | **`analytics/tools.py` registers `analytics_query` and `analytics_report` MCP tools** via `register_analytics_tools(mcp)`, but `server.py` never imports or calls that function. **Dead code that the MCP server silently doesn't expose.** Either wire it in `server.py:102` after `register_composio_tools(mcp)` or delete the file. |
| 8 | 🟠 High | Architecture | **`telemetry/__init__.py` is a god-package with 7 star-imports** (`from .records_ai import *`, …) and a 100+ name `__all__`. Every consumer of `..telemetry` (18 import sites) pays the full import cost on cold start. No `telemetry/AGENTS.md`. |
| 9 | 🟡 Medium | Architecture | **AGENTS.md drift in `rerank/` and `search/`.** `rerank/AGENTS.md` lines 9-15 list `stack.py, policy.py, diversity.py` — none exist. `search/AGENTS.md` documents 10 modules; the package has 30+ files (missing: `provider_catalog.py`, `provider_call.py`, `diagnostics.py`, `normalize.py`, `entity_extractor.py`, `gemini_search_tool.py`, `intents.py`, `merge_observability.py`, plus `academic/` and `understanding/` sub-packages). Root `AGENTS.md` is missing links for `telemetry/`, `llm/`, `evals/`, `youtube/`, `config/`, `contracts/`, `dashboards/`. |
| 10 | 🟡 Medium | Coupling | **`content/` reaches into `search/normalize.canonicalize_url` from 6 files** (`content/fetch_pipeline.py:25,170`, `content/batch_orchestrator.py:10`, `content/firecrawl_stage.py:14`, `content/link_discovery.py:6`, `content/specialized_pipeline.py:13`, `content/stages.py:37`). The two packages are presented as parallel siblings; in practice content has a single hidden dep on a search-internal helper that isn't documented in `search/AGENTS.md`. |

Plus 5 medium and 8 low-priority deltas — see full reports.

---

## CLI Exercise — what actually works (good)

These are the high-confidence "this is healthy" signals from running the CLI. ~20 commands exercised end-to-end.

| Command | Status | Notes |
|---|---|---|
| `web-search-cli doctor` | ✅ all green | `package_importable`, `typer_importable`, `user_skill`, `dev_skill`, `duckdb_cli`, `phoenix_instrumentor`, `repo_root` — all `ok: true`. |
| `web-search-cli schema` | ✅ works | Full Typer tree as JSON. Matches the SKILL.md table. |
| `web-search-cli reference tools --profile full` | ✅ works | 12 MCP tools mapped to CLI commands. |
| `web-search-cli reference external-tools` | ✅ works | Lists DuckDB / Grafana (WSL) / Phoenix CLI as companion tools. |
| `web-search-cli search web` | ✅ works | 15 results across `brave`, `ddg`, `langsearch`, `brightdata`. Per-result: `providers`, `provider_count`, `score`, `provider_consensus_rrf_score`, `cross_relevance_score`, `hybrid_rrf_score`. Graceful `warnings` array for failed providers (gemma/searxng/degoog/serpapi/qdrant/composio_llm_search). |
| `web-search-cli content get` | ⚠️ works BUT page_content bug (#2) | Windowing + cache hit on second call (`source_type: cache`, `has_more: true`, `next_offset: 4587`). Successful fetch of `https://github.com/jlowin/fastmcp` via `crawl4ai_remote` backend. |
| `web-search-cli content batch` | ❌ fails with `ModuleNotFoundError: No module named 'firecrawl'` | See #6. |
| `web-search-cli links discover` | ✅ works | 20 links with `internal: bool`, `domain`, `text`; metadata block with `title`, `description`, `site_name`, `language`. |
| `web-search-cli links similar` | ✅ works | Composio-backed, returns score-sorted similar links. |
| `web-search-cli search academic` | ✅ works | 3 papers from arxiv (all 2025/2026), with `warnings: [{provider: semanticscholar, error_type: empty_results}]` — clean provider-level diagnostics. |
| `web-search-cli search quick` (Composio/Exa) | ❌ fails with `COMPOSIO_SEARCH_TAVILY: HTTP 402` | The OTel banner appears AFTER the JSON envelope in this case (non-deterministic ordering). |
| `web-search-cli ai gemini` | ✅ graceful failure | All 3 fallback models exhausted, but the response is a structured 200 OK with `fallback_chain: ["gemini-3.1-flash-lite", "gemini-2.5-flash", "gemini-2.5-flash-lite"]` and `fallback_reason: rate_limit: 429 RESOURCE_EXHAUSTED`. |
| `web-search-cli ai grok` | ❌ OpenRouter 402 Payment Required | OpenRouter account has no credits. |
| `web-search-cli youtube search` | ✅ works | YouTube Data API v3 (3 results, `search_backend: api`, `providers: ["youtube_api"]`). |
| `web-search-cli analytics report provider-performance` | ✅ works | 14-row DuckDB-backed table. Strong real-world signal: `gemma` 0% success, `degoog` 16%, `brightdata_*` ~28%, while `ddg`/`brave`/`langsearch` are 96-99%. |
| `web-search-cli analytics report error-taxonomy` | ✅ works | 26 rows. Top error: `gemma: retrieve_budget` (76 occurrences), plus a smoking gun: `chain_failed: ModuleNotFoundError` 25 times in `rerank_stage` — the `rankllm` dep is missing. |
| `web-search-cli analytics report latency-breakdown` | ❌ SQL binder error (#5) | Reproducible. |
| `web-search-cli analytics query` | ✅ works | Natural language → SQL. Question classifier supports `rerank, latency, run quality, provider, error, eval, recent events`. Rejects "yesterday" as ambiguous — good guardrail. |
| `web-search-cli experiments list/create/stats/disable/conclude` | ✅ works | Full CRUD. Validates `traffic_pct in (0,100]` and `≥ 2 variants`. Created and concluded `smoke-test-001` cleanly. |
| `web-search-cli sitemap generate` | ✅ works | 5 URLs in 80ms via Tavily/Map backend. |
| `web-search-cli getskill` | ✅ works | 732 lines of SKILL.md echoed verbatim. |
| `web-search-cli server start --stdio` | ✅ correct refusal | Plain-text error: "stdin/stdout JSON-RPC". Intentionally rejects running in a non-MCP shell. **But:** the error is not JSON-enveloped and the OTel banner still leaked. |

---

## Architecture findings (from `01-architecture.md`)

**Layering holds.** No import cycles. Direction is clean:
- `tools/` → `search/`, `content/`, `cache/`, `analytics/`, `telemetry/`, `llm/`
- `content/` → `search/normalize` (one-way coupling — see Risk #2)
- `cli/services/` bypasses `tools/` and calls `search.service.execute_web_search` directly (intentional, per `search/AGENTS.md`)
- `analytics/`, `observability/`, `telemetry/`, `cache/`, `content/`, `rerank/`, `tools/`, `cli/`, `server.py` all reach downward — never upward.

**Entry points all match AGENTS.md:** `server.py` (root), `src/.../server.py`, `__main__.py`, `cli/app.py`, `classifier_service/server.py`.

**The 5 architectural risks (from the audit):**

1. `telemetry/` god-package with star-imports and 100+ name `__all__`. High cold-start cost. No `telemetry/AGENTS.md`. **See #8.**
2. `content/` → `search/normalize.canonicalize_url` coupling. **See #10.**
3. AGENTS.md drift. **See #9.** Specific drift items:
   - `rerank/AGENTS.md` lists `stack.py, policy.py, diversity.py` — none exist.
   - `embeddings/AGENTS.md` claims `BatchLimitedEmbeddings` is public; `__init__.py` doesn't re-export it.
   - `root AGENTS.md` Package Guides list omits 8 directories.
4. `analytics/tools.py` defines `@mcp.tool` decorators but `register_analytics_tools` is never called from `server.py`. **Dead MCP tools.** **See #7.**
5. Hidden global state + manual shutdown sequencing in `tools/_helpers.py` for 6 subsystems. Replacement: context-manager stack or `lifecycle.register(...)`.

**Public surface bloat:**
- `youtube/__init__.py:56` re-exports private `_parse_iso8601_duration`.
- `cache/__init__.py` exports `QUERY_CACHE_DEFAULT_*` constants — looks like config, lives in a cache facade.
- `analytics/__init__.py` re-exports 11 implementation fragments; consider an `AnalyticsClient` facade.

**Dead code / orphans:**
- `tmp7k1_elq9.py`, `tmpzum42dot.py` at repo root (untracked, debug harnesses).
- `MagicMock/` (~1.8MB, gitignored but visible).
- `dashboard/` (untracked Streamlit app) and `dashboards/` (tracked Streamlit app) — two apps for one purpose.
- `analytics/writers/migrations.py` — AGENTS.md itself says "no longer used".
- `src/classifier_service/` is a separate service that must be manually started; without it the `onnx_classifier` silently degrades to the LLM resolver. The "primary path" comment is misleading.

---

## Code Quality findings (from `02-code-quality.md`)

**Linting:** `ruff check src/...` → **5 fixable findings** across 305 files. The codebase is in great shape on lint.

**Top 10 largest files** (lines, public surface):
| # | File | Lines | Public |
|---|------|------:|--------|
| 1 | `settings.py` | 591 | `Settings` (~70 fields) |
| 2 | `content/summary_backend.py` | 545 | Gemini source-grounded summarization |
| 3 | `search/gemini_search_tool.py` | 495 | Dual-prompt grounding with 3-tier model fallback |
| 4 | `tools/content.py` | 464 | 3 MCP entrypoints |
| 5 | `analytics/writers/schema.py` | 441 | 12 fact tables + HNSW bootstrap |
| 6 | `content/resolvers/github_discussions.py` | 428 | GraphQL + 150-line markdown renderer |
| 7 | `cli/commands/experiments.py` | 415 | 8 typer sub-commands |
| 8 | `analytics/views.py` | 412 | 10 dashboard SQL view builders |
| 9 | `search/providers/grok.py` | 388 | OpenRouter/Grok with native web + x_search |
| 10 | `search/academic/academic_search_orchestrator.py` | 386 | 6 sources, dedup by DOI/ArXiv/PMID/title |

**5 high-complexity functions, each warrants a focused refactor:**

1. `plan_search` (`search/planning.py:203-356`, ~25 branches). 6 `QueryBranch` literals each gated by 3-condition ternaries.
2. `retrieve_branches` (`search/retrieval.py:182-340`, ~20 branches). 4 nearly-identical `_record_provider_result(...)` callsites with different kwargs.
3. `gemini_search_with_grounding_dual` (`search/gemini_search_tool.py:528-end`, ~15 branches). Tier loop × try/except × per-model retry.
4. `render_discussion_thread_markdown` (`content/resolvers/github_discussions.py:62-end`, ~30+ branches). Linear `isinstance` guards.
5. `gemini_search_with_grounding` + `_call_single_grounding` (`search/gemini_search_tool.py:366-403` + `:505-526`).

**Async hygiene:**
- `youtube/yt_dlp_backend.py:130` — sync `httpx.get` inside async path. **Actual blocking I/O on the event loop.**
- `cli/runtime.py:156` — `asyncio.run(...)` in `lifecycle()`. Not inside a running loop today, but any future MCP handler caller would deadlock.
- `embeddings/hf_inference.py:304` — stale `# type: ignore[name-defined]` on `retry_delay` (defined at L265).

**Error handling is mostly good** — `cli/commands/experiments.py` uses `CliError`/`ExitCode` consistently. But:
- `cli/services/content_batch.py:80` — `except Exception: pass` silently swallows the per-item `astore` failure. **This is the bug that would mask the `firecrawl` missing-dep issue if it ever silently fell back instead of erroring.**
- `analytics/app_queries.py:56,65,248` — three `except Exception: return []` with no logging.
- `cache/page_cache.py` — five `except Exception as exc: LOGGER.warning(...)` blocks; "any failure → warn and proceed" silently degrades a corrupted cache to misses.

**Logging hygiene:**
- `search/gemini_search_tool.py:386-462` interpolates raw `exc` — for Gemini 4xx errors, the stringified exception can include the request body. Should be `type(exc).__name__` + redacted message.
- `utils/observability.py:321` serializes full normalized tool payloads; `page_content`/`answer` are redacted via `preview_text` (good) but `metadata` is verbatim.
- Inconsistent log levels in `cache/page_cache.py` — `warning` for lookup failures at L51, `debug` for the same condition at L160.

**Type-hint coverage gaps:**
- `composio_tools.py` — 7 defs, 0 with return types.
- `cli/app.py:31` — `global_options` and `main` lack `-> None`.
- `analytics/app_queries.py` — 6 defs, 0 with return types.

**Refactor candidates (3, scoped):**
1. `plan_search` → branch-table builder (~150 → ~70 lines).
2. Six-arity `_record_provider_result` → strategy map with `OutcomeKind` enum.
3. Replace `except Exception: pass` in `cli/services/content_batch.py:78-83` with a debug log.

---

## Combined deltas (ordered, actionable)

### P0 — must-fix bugs (do these first)

1. **Fix OTel banner leak to stdout.** Source is in `telemetry/init.py` or wherever `register(...)` is called at module import. Move the banner to stderr, or gate behind `--debug`, or lazy-init after the first command. Confirm with `web-search-cli doctor 2>/dev/null | python -c "import json,sys; json.load(sys.stdin)"` — should succeed.
2. **Fix `content get page_content` repr() bug.** Find the JSON encoder call (likely `cli/services/content.py:36` `_cached_artifact` or `_artifact_from_fetch_exception`); the `page_content` field is being passed through `repr(dict)` instead of `json.dumps(dict)`.
3. **Update SKILL.md `search web` table:** remove the `--num-results` row and the example. Add a note that result count is determined by `--domain-boost`/`--domain-block` plus the merge/rerank pipeline.
4. **Update SKILL.md `analytics report` example:** replace `--report-name "provider_health"` with a real name. Add the list of 7 valid reports.
5. **Fix `analytics report latency-breakdown` SQL.** The error is `Binder Error: Could not ORDER BY column "CASE WHEN ((stage = 'total')) THEN (1) …"`. The `ORDER BY` references a CASE expression that isn't in either leg of the UNION. Move the CASE to a SELECT alias or push the UNION into a FROM clause.
6. **Wire `register_analytics_tools(mcp)` in `server.py:102`** after `register_composio_tools(mcp)`, OR delete `analytics/tools.py`. (Currently `analytics_query` and `analytics_report` are dead MCP tools.)

### P1 — important cleanups

7. **Update `cli/commands/content/batch.py` error path** to (a) clearly say "firecrawl module not installed — `pip install firecrawl-py` or set FIRECRAWL_API_KEY", and (b) emit a proper JSON error envelope. (c) Add `firecrawl` to the `web-search-cli doctor` checks.
8. **Replace `telemetry/__init__.py` star-imports** with explicit re-exports of the ~10 actually-used functions. Add `telemetry/AGENTS.md`.
9. **Fix `content/observability_*` files.** Either move the 6 `observability_*` files out of `analytics/` into a proper `observability/` package (with `__init__.py`), or rename them in `analytics/` to avoid the cross-package name collision with the real `observability/` package.
10. **Regenerate `search/AGENTS.md` and `rerank/AGENTS.md`** against actual `ls`. Remove references to `stack.py`/`policy.py`/`diversity.py` in rerank. Add the 11+ missing files to search.
11. **Move `search/normalize.canonicalize_url` to `utils/url_canonicalize.py`** and update the 6 call sites in `content/`. Add `embeddings/__init__.py` re-export of `BatchLimitedEmbeddings`.

### P2 — polish

12. Delete `tmp7k1_elq9.py`, `tmpzum42dot.py`, `MagicMock/`, `analytics/writers/migrations.py`. Decide whether `dashboard/` (untracked) or `dashboards/` (tracked) is the canonical Streamlit app; delete the other.
13. Remove `youtube/__init__.py:56` private re-export `_parse_iso8601_duration`.
14. Delete the stale `# type: ignore[name-defined]` in `embeddings/hf_inference.py:304`.
15. Fix the 3 unused imports in `contracts/base.py:16-17` (auto-fixable with `ruff --fix`).
16. Replace `except Exception: pass` in `cli/services/content_batch.py:80` with a `LOGGER.debug(...)`.
17. Investigate the `rewrite-effectiveness` report row where `rewrite_enabled: true, rewrite_model: "none"` shows `rewrite_error_rate_pct: 80.0` — likely a join key bug.
18. Fix the Gemini 4xx logging in `search/gemini_search_tool.py:386-462` — redact request body, log `type(exc).__name__` only.
19. Investigate the `chain_failed: ModuleNotFoundError` rerank error (25 occurrences) — `rankllm` dep appears to be missing.
20. Investigate why `gemma`, `degoog`, `brightdata_bing`, `brightdata_yandex` are 0-29% success — likely budget/quota issues, but worth a config review.

---

## What's NOT covered in this report

- **Security review** — explicitly omitted per user direction. The CLI exercise did surface some hygiene concerns (OTel banner exposing the OTel transport header shape, HuggingFace `AsyncInferenceClient.__del__` traceback leak), but a full source-level security audit was not performed.
- **Performance / load testing** — not in scope.
- **Tests audit** — still in progress (`04-tests.md` pending). The CLI exercise was effectively a black-box integration test, but per-command unit/integration coverage was not assessed.

---

## Files in this directory

```
.assessment-out/
├── 00-SYNTHESIS.md          ← you are here
├── 01-architecture.md        ← 24.5KB, full module map + layering + drift
├── 02-code-quality.md        ← 10.7KB, complexity + async + logging + types
├── 04-tests.md              ← in progress
├── cli-search-web-2.json    ← 15 results across 4 providers
├── cli-content-get.json     ← page_content repr() bug demo
├── cli-content-get-paginated.json  ← cache hit + pagination
├── cli-content-batch.json   ← ModuleNotFoundError: firecrawl
├── cli-analytics-provider-perf.json
├── cli-analytics-error-taxonomy.json
├── cli-analytics-rewrite.json
├── cli-analytics-latency.json   ← SQL binder error demo
├── cli-analytics-query.json
├── cli-analytics-latency-query.json
├── cli-ai-gemini.json       ← graceful 3-tier fallback
├── cli-ai-grok.json         ← 402 Payment Required
├── cli-youtube-search.json
├── cli-links-discover.json
├── cli-links-similar.json
├── cli-sitemap.json
├── cli-search-academic.json
├── cli-search-quick.json
├── cli-experiments-*.json   ← full CRUD cycle
├── cli-external-tools.json
├── cli-getskill.txt
└── cli-bare-stdout.txt      ← shows the OTel banner in raw form
```

