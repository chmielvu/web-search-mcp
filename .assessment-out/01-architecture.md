# Architecture & Structure Review — `web-search-mcp`

Scope: 305 Python files under `src/kindly_web_search_mcp_server/` and `src/classifier_service/`. Evidence drawn from AGENTS.md files (root + per-package), `git ls-files`, `grep` import graphs, and the GitNexus index (`web-search-mcp`, 7162 symbols).

## 1. Module map — stated vs. actual public surface

| Package | AGENTS.md claim | Actual `__init__.py` re-exports | Drift |
|---|---|---|---|
| `src/kindly_web_search_mcp_server/` (root) | n/a | `__init__.py` is one-line docstring; no re-exports | clean |
| `search/` (search/AGENTS.md) | "planning, provider, retrieval, ranking modules… no registration or provider-client side effects on import" | `__init__.py` is docstring + `__all__: list[str] = []` (no re-exports) | clean — `__init__.py` confirms "import-light" promise |
| `search/providers/` | "Each module exposes an async `search_<name>`… registered in `provider_catalog.py`" | `__init__.py` is docstring, no re-exports | clean |
| `search/academic/` | n/a (claimed in root list) | `__init__.py` is docstring, no re-exports | clean |
| `search/understanding/` | n/a | re-exports `QueryUnderstanding`, `QueryUnderstandingResult` | clean |
| `content/` | "Two-tier pipeline" | `__init__.py` is docstring only | clean |
| `content/resolvers/` | (no AGENTS.md; inherited from content/) | `__init__.py` missing — directory is imported as a namespace package | minor: no `__init__.py` |
| `tools/` | "Tool catalog and visibility profile helpers. Actual MCP tool implementations live in `server.py`" | `__init__.py` is one-line docstring only | clean |
| `cli/` | "Native Typer CLI"; `__init__.py` re-exports `main` | `from .app import main; __all__ = ["main"]` | clean |
| `cli/commands/` | "Command registration modules" | `__init__.py` is `from __future__ import annotations` only | clean |
| `cli/services/` | "Shared service adapters" | `__init__.py` is `from __future__ import annotations` only | clean |
| `cache/` | n/a (re-exports listed in docstring) | re-exports `ExactQueryCache`, `PageCache`, `TranscriptCache`, etc. | clean |
| `embeddings/` | "public surface is `embed_query`, `embed_texts`, `EMBEDDING_DIM`, and `BatchLimitedEmbeddings`" | re-exports only `embed_query, embed_texts, EMBEDDING_DIM` | **drift** — `BatchLimitedEmbeddings` (claimed in AGENTS.md L21) is NOT in `__all__`; AGENTS.md says "no local fallback" but `rate_limiter.py` is the per-caller wrapper |
| `rerank/` | (n/a in current rerank/AGENTS.md) | `__init__.py` is one-line docstring | clean |
| `prompts/` | n/a | re-exports `build_prompt` | clean |
| `middleware/` | n/a | re-exports `ExpensiveToolProtectionMiddleware`, `DynamicGuidanceMiddleware`, `DifferentiatedRateLimitMiddleware` + helpers | clean |
| `entity/` | "Public surface: EntitySpan, DEFAULT_*_LABELS, chunk_text, postprocess_entities" | re-exports `EntitySpan, DEFAULT_QUERY_LABELS, DEFAULT_CONTENT_LABELS, chunk_text, postprocess_entities` | clean |
| `training/` | n/a | re-exports `append_query_outcome_record`, `append_query_understanding_record`, `SessionStateStore` | clean |
| `index/` | n/a | re-exports `WebResultsIndex`, `get_web_results_index`, `index_final_results`, `encode_bm25` | clean |
| `llm/` | n/a (no AGENTS.md) | re-exports `LLMWorker`, `build_llm_worker`, `StructuredLLMRequest/Response`, `LLMUsage` | clean |
| `evals/` | n/a (no AGENTS.md) | re-exports `EvalCase`, `run_dataset`, four judge_* functions, five metric helpers, `MCPEVAL_AVAILABLE` | clean |
| `telemetry/` | n/a (no AGENTS.md) | wildcard re-exports from 8 sub-modules + 100+ constants in `__all__` | **bloat — see §5** |
| `youtube/` | n/a (no AGENTS.md) | re-exports 23 names including private `_parse_iso8601_duration` | **bloat — see §5** |
| `analytics/` | (per analytics/AGENTS.md) | re-exports `append_event`, `ensure_store_schema`, `build_analytics_query_plan`, `run_analytics_query`, `available_reports`, `run_report`, `sync_once`, plus `build_eval_*` | clean |
| `observability/` | n/a (claim: "intentionally small… shared event shapes") | **NO `__init__.py`** — only `events.py` + `AGENTS.md`. Imports work via `from ..observability.events import PERSISTED_EVENT_PREFIXES` (`utils/observability.py:10`) | **drift** — AGENTS.md says package is shared, but only `events.PERSISTED_EVENT_PREFIXES` is used. No public surface declared. |
| `config/` | not in root AGENTS.md link list | only `base.py` (no `__init__.py`); used via `from .config import …` in `settings.py` | undocumented in root AGENTS.md |
| `contracts/` | not in root AGENTS.md link list | only `base.py` (no `__init__.py`); defines `StrictBase`, `JsonTuple` | undocumented in root AGENTS.md |
| `ab_testing/` | n/a | `__init__.py` is docstring only | clean |
| `utils/` | n/a | `__init__.py` is docstring only | clean |
| `src/classifier_service/` | "Separate HTTP service" | only `server.py` + `runtime.py` + `Dockerfile` + `requirements.txt`; no `__init__.py` | clean |

## 2. Layering review

Direction (claim → reality):

- **tools → search / content / cache / analytics / telemetry / llm**: every tool module imports its backend. `tools/content.py:11-22` imports `..content.batch_orchestrator`, `..content.fetch_pipeline`, `..content.link_discovery`, `..content.options`, `..content.summary`, `..content.windowing`, `..search.normalize`. `tools/ai_search.py:12-20` imports `..analytics.judge_runner`, `..search.gemini_search_tool`, `..search.providers.grok`, `..telemetry`. **No upward leak from search/content back into tools.**
- **content → search**: `content/fetch_pipeline.py:25`, `content/batch_orchestrator.py:10`, `content/firecrawl_stage.py:14`, `content/link_discovery.py:6`, `content/specialized_pipeline.py:13`, `content/stages.py:37` all import `..search.normalize.canonicalize_url`. Plus `content/fetch_pipeline.py:170` does a deferred `from ..search.entity_extractor import extract_entities`. **content reaches into search for `normalize` (canonicalize_url) — one-way, but creates coupling between two "tier" siblings that AGENTS.md presents as parallel.**
- **search → content**: none. `grep "from \.\.content\|import \.\.content"` in `search/` returns zero matches. ✅
- **cli → tools / search / content**: `cli/services/search_web.py:6-10` imports `...search.contracts`, `...search.options`, `...search.service`. CLI bypasses `tools/` and reaches into `search/` directly. Per `search/AGENTS.md` line 47: *"MCP `tools/search.py` and CLI `cli/services/search_web.py` must both construct `WebSearchRequest` and call `execute_web_search`"* — so this is intentional. But the CLI never goes through the `tools/` adapter at all; the `cli/services/*` files are a parallel adapter layer.
- **analytics reach-in**: `tools/ai_search.py:12` → `..analytics.judge_runner`. `tools/_helpers.py:14` → `..analytics.async_writes.shutdown_duckdb_write_executor`. `server.py:54` → `.analytics.app` (mounts a sub-app). `cli/commands/analytics.py:7-9` → `..errors`, `..exit_codes`, `..output`. All downward. ✅
- **observability reach-in**: `utils/observability.py:10` → `..observability.events.PERSISTED_EVENT_PREFIXES`. `cache/page_cache.py:14` → `.observability.emit_cache_lookup_event`. All downward. ✅
- **telemetry reach-in**: 18 import sites; every site reaches into `..telemetry` from `cache/`, `content/`, `rerank/`, `search/`, `tools/`, `cli/`, `server.py`. Heavy fan-in. ✅ direction is correct but `telemetry/` is doing a lot (see §5 / §8).
- **cycles**: none observed in the import scan above. The only tight loop is `analytics/observability_tables.py:5-6` ↔ `observability_inserts.py:18` ↔ `observability_schema.py` (all within `analytics/`), which is fine.

## 3. Entry points — confirmed against AGENTS.md

| AGENTS.md claim | Actual file | First lines / role | Verdict |
|---|---|---|---|
| `server.py` (root wrapper for FastMCP launchers) | `server.py:1-9` | `from kindly_web_search_mcp_server.server import mcp` (no relative imports — for `fastmcp run server.py`) | ✅ matches |
| `src/kindly_web_search_mcp_server/server.py` (main MCP server) | `src/.../server.py:25-130` | imports `.composio_tools`, `.analytics.app`, registers `@mcp.tool(...)` for 10 tools + composio + 3 resources + 1 sub-app | ✅ matches |
| `src/kindly_web_search_mcp_server/__main__.py` (package entrypoint) | `__main__.py:1-10` | defers to `.server.main` | ✅ matches |
| `src/kindly_web_search_mcp_server/cli/app.py` (`web-search-cli`) | `cli/app.py:1-31` (147 lines) | Typer `app = typer.Typer(...)`, imports from `.commands`, `.errors`, `.exit_codes`, `.metadata`, `.output`, `.runtime`, `..utils.logging` | ✅ matches |
| `src/classifier_service/server.py` (separate intent-classifier service) | `src/classifier_service/server.py` (exists, used as HTTP target by `search/understanding/onnx_classifier.py:32` via `settings.intent_classifier_url`) | ✅ matches — but see §6 about "unused at runtime" |

## 4. AGENTS.md drift — concrete contradictions

| Where | Claim | Reality | Evidence |
|---|---|---|---|
| `rerank/AGENTS.md` lines 9-15 | Lists `stack.py, policy.py, diversity.py` in structure | None of these files exist in `src/.../rerank/`; actual files include `bm25.py, conditional_bi.py, limits.py` which are NOT in the AGENTS.md tree | `ls src/.../rerank/*.py` |
| `rerank/AGENTS.md` line 16 | "`cohere.py, openrouter.py, voyage.py` / `jina.py`" as siblings under structure | All four present | ✅ |
| `search/AGENTS.md` lines 7-13 | Lists 10 modules (service, contracts, planning, provider_registry, retrieval, ranking, outcomes, merge, blocklist, intent_policy) | 11 more files exist and are not documented: `provider_call.py, provider_catalog.py, diagnostics.py, normalize.py, entity_extractor.py, gemini_search_tool.py, intents.py, merge_observability.py, academic/, understanding/` | `ls src/.../search/*.py` |
| `search/AGENTS.md` line 47 | "MCP `tools/search.py` and CLI `cli/services/search_web.py` must both construct `WebSearchRequest` and call `execute_web_search`" | True — `cli/services/search_web.py:6-10` and `tools/search.py:11-12` both import `WebSearchRequest` / `build_search_options` and call `execute_web_search` (verified) | ✅ |
| `embeddings/AGENTS.md` line 21 | "The public surface is `embed_query`, `embed_texts`, `EMBEDDING_DIM`, and `BatchLimitedEmbeddings`" | `embeddings/__init__.py` re-exports only the first three; `BatchLimitedEmbeddings` is not in `__all__` | `embeddings/__init__.py:5-12` |
| `root AGENTS.md` Package Guides list | Missing links for: `telemetry/`, `llm/`, `evals/`, `youtube/`, `config/`, `contracts/`, `dashboard/`, `dashboards/`, `searxng/`, `classifier_service/` Dockerfile, `tests/cli/` | These directories exist and are used; only `classifier_service` is linked | `ls src/...` + `root AGENTS.md` lines 36-56 |
| `cli/AGENTS.md` line 26 | `youtube.py` listed under `cli/services/` | File exists as `cli/services/youtube.py` | ✅ |
| `analytics/AGENTS.md` line 12 | Mentions `migrations.py` "Legacy search_events backfill (no longer used)" | `analytics/writers/migrations.py` exists | ✅ accurate, but the file is still on disk and the note flags it as dead |
| `content/AGENTS.md` lines 25-45 | Two-tier pipeline; `batch_orchestrator.py handles multi-URL fetches via per-URL fetch_content_artifact calls` | True | ✅ |
| `cache/AGENTS.md` line 19 | "`page_cache.py` and `transcript_cache.py` delegate to DuckDB backends" | True | ✅ |
| `content/AGENTS.md` line 27 | "Camoufox last-resort browser fallback" | `content/remote_clients.py` is documented; `close_camoufox_client` exists (`tools/_helpers.py:16`) | ✅ |
| `docs/AGENTS.md` | References `crawl4ai-research.md, firecrawl-batch-scrape-plan.md` | These files exist | ✅ |
| `index/AGENTS.md` | "Write-only remote Qdrant web-results index" | True | ✅ |

## 5. Public surface bloat

- **`telemetry/__init__.py` (185 lines)**: uses `from .records_ai import *`, `from .records_circuit import *`, `from .records_content import *`, `from .records_core import *`, `from .records_rerank import *`, `from .span_enhancements import *`, `from .spans import *` (lines 12-19). The `__all__` is 100+ names. This pulls every downstream importer into a heavy import chain (record types, span builders, metrics helpers, ~30 semantic-convention constants). The `telemetry/AGENTS.md` does not exist to declare intent. Concretely: a routine `from ..telemetry import record_cache_lookup` in `cache/page_cache.py:13` triggers the full `records_*` + `spans` + `span_enhancements` + `attributes` import graph.
- **`youtube/__init__.py`**: re-exports `_parse_iso8601_duration` (line 56, leading underscore = private). Defined in `youtube/api_enrichment.py`; should remain an internal helper.
- **`cache/__init__.py`**: re-exports `provider_cache_key` (a small helper) together with cache classes, which is fine, but also exports `QUERY_CACHE_DEFAULT_MAX_ENTRIES` and `QUERY_CACHE_DEFAULT_TTL_SECONDS` — constants that look like config but live in a cache facade. Minor.
- **`prompts/__init__.py`**: only re-exports `build_prompt`. Clean. ✅
- **`analytics/__init__.py`**: re-exports 11 names including `sync_once` (MotherDuck), `ensure_eval_tables`, `build_analytics_query_plan`. These are plumbing; if any downstream code is supposed to use a single `AnalyticsClient` facade, this surface re-exports implementation fragments.

## 6. Dead code / orphan modules

- **`tmp7k1_elq9.py` and `tmpzum42dot.py`** at repo root — untracked (per `git ls-files`), are debug harnesses for FastMCP plan_search. Not in `pyproject.toml`, not in `src/`. Should be deleted; if useful, move to `scripts/` or a `tests/manual/` dir.
- **`MagicMock/`** — gitignored (`.gitignore:355`) but visible on disk; ~1.8 MB binary files. Looks like a debug directory accidentally created. Not in source.
- **`.ruff_cache/`, `.gitnexus/`, `.cocoindex_code/`, `searxng/`, `searxng-settings/`** — all gitignored local artifacts. Not in source.
- **`analytics/tools.py`** — defines `register_analytics_tools(mcp)` and registers `analytics_query` + `analytics_report` MCP tools (`@mcp.tool(...)` at lines 21 and 46), but `server.py` does NOT import or call `register_analytics_tools` (verified by `grep "register_analytics_tools\|analytics\.tools" server.py` → no matches). The tools are defined but **never registered**, so they are dead. Either delete the file or wire it up in `server.py`.
- **`analytics/writers/migrations.py`** — AGENTS.md itself flags as "no longer used". Dead; safe to remove.
- **`content/observability_inserts.py` / `observability_store.py` / `observability_tables.py` / `observability_schema.py` / `observability_ids.py` / `observability_rows.py`** — six files prefixed `observability_` living in `analytics/`. AGENTS.md only mentions `observability_schema.py` and `observability_store.py`. The other four (`observability_inserts`, `observability_tables`, `observability_ids`, `observability_rows`) are not documented. Likely active but the naming is confusing — they live in `analytics/`, not the `observability/` package.
- **`src/classifier_service/` runtime use** — at runtime the MCP server calls `search/understanding/onnx_classifier.py` which POSTs to `settings.intent_classifier_url` (default `http://127.0.0.1:18686`). The service exists separately and is not auto-started. If the host never runs `classifier_service/server.py`, the classifier always returns `None` and the search pipeline silently falls through to the LLM-based `understanding/resolver.py` (`onnx_classifier.py:30-40`). This is by design (graceful degradation) but the "intent classifier is the primary path" comment at `onnx_classifier.py:7` is misleading.
- **`search/providers/brightdata.py`, `telegram.py`, `telegram_client.py`, `telegram_registry.py`, `composio_llm_search.py`, `qdrant.py`, `gemma_serp.py`** — provider modules; cannot confirm from static reading whether all are wired into `provider_catalog.PROVIDER_DEFINITIONS_LIST`. Verify with `gitnexus impact` on `ProviderDefinition` if you intend to prune.
- **`dashboard/`** (untracked directory, has `app.py`, `README.md`, `requirements.txt`, `pages/`, `core/`, `rag/`, `.streamlit/`) — exists at repo root but is not in `git ls-files`. Local-only.
- **`dashboards/`** (different folder; tracked) — has `app.py`, `db.py`, `Dockerfile`, `mockup/streamlit_app.py`, `requirements.txt`. This is the canonical "Search Quality Dashboard" (per `dashboards/app.py:1-9`). `dashboard/` and `dashboards/` are two separate streamlit apps with overlapping purpose; only `dashboards/` is tracked.

## 7. Tool / MCP layering

- **MCP tool registration locations** (from `grep "@mcp\.tool\|mcp\.tool("`):
  - `server.py:112-121` — 10 tools (`web_search`, `get_content`, `batch_get_content`, `discover_links`, `gemini_search`, `grok_search`, `youtube_transcript`, `youtube_search`, `generate_sitemap`, `academic_search`)
  - `composio_tools.py:163,176` — 2 tools (`quick_web_search`, `composio_similarlinks`) — registered via `register_composio_tools(mcp)` at `server.py:102`
  - `analytics/tools.py:21,46` — 2 tools (`analytics_query`, `analytics_report`) — **LEAKAGE: never registered** (see §6)
  - `server.py:129-131` — 3 MCP resources (`analytics://schema`, `analytics://candidate-survival`, `analytics://reports/{report_name}`)
  - `server.py` mounts `.analytics.app` as a sub-MCP app at line 54 (via `providers=[analytics_app]`)
- **Conclusion**: `tools/` is the catalog/visibility/registration-glue layer, but actual `@mcp.tool` decorators live in `server.py` (10) + `composio_tools.py` (2) + `analytics/tools.py` (2, **dead**). The AGENTS.md claim "Actual MCP tool implementations live in `server.py`" is half-true: 2 composio tools and 2 analytics tools live elsewhere. `analytics/tools.py` is the only **broken** one (never called).
- **CLI command registration locations** (from grep in `cli/`):
  - `cli/commands/*.py` — 13 files: `ai.py, analytics.py, content.py, doctor.py, experiments.py, getskill.py, links.py, reference.py, schema.py, search.py, server.py, sitemap.py, youtube.py`. All defined here. ✅
  - `cli/app.py:11-25` imports them via `from .commands import (...)`. ✅
- **CLI service adapters** (parallel to MCP tools) — `cli/services/*.py`: 10 files (`academic, ai, content, content_batch, link_tools, quick_search, search_web, sitemap, youtube`). These are real adapters; they bypass `tools/` and call `search.service.execute_web_search` and `content.fetch_pipeline.fetch_content_artifact` directly. By design (per `search/AGENTS.md`).
- **Conclusion**: CLI command structure is clean. MCP tool structure has minor leakage (composio_tools.py, analytics/tools.py). Fix: either (a) fold the 2 composio tools into `server.py`, or (b) document `composio_tools.py` and `analytics/tools.py` in `tools/AGENTS.md` and wire the latter.

## 8. Top 5 architectural risks

1. **`telemetry/` is a god-package with star-imports.** `telemetry/__init__.py:12-19` does `from .records_ai import *`, `from .records_circuit import *`, `from .records_content import *`, `from .records_core import *`, `from .records_rerank import *`, `from .span_enhancements import *`, `from .spans import *`. Any consumer of `..telemetry` (18 import sites, including `cache/`, `content/`, `rerank/`, `search/`, `tools/`, `server.py`) pays the import cost of every record module + `_internal.py` + `httpx` + (transitively) the OTEL SDK via the `constants._OTEL_SDK_AVAILABLE` probe. The package has no `AGENTS.md` and no `__all__` discipline. Cold-start warm-up (`server.py:_warm_heavy_imports()`) only works if the import graph is fully explicit; wildcard re-exports make this brittle. Concrete delta: split `telemetry/` into `telemetry/metrics.py`, `telemetry/spans.py`, `telemetry/constants.py` and replace the `__init__.py` star-imports with explicit re-exports of the ~10 functions actually used across the codebase (record_*, create_*_span, init_telemetry).

2. **`content/` reaches into `search/normalize` for `canonicalize_url` from 6 files** (`content/fetch_pipeline.py:25,170`, `content/batch_orchestrator.py:10`, `content/firecrawl_stage.py:14`, `content/link_discovery.py:6`, `content/specialized_pipeline.py:13`, `content/stages.py:37`). The content package is otherwise self-contained; the AGENTS.md presents `content/` and `search/` as parallel siblings, but in practice every content module depends on a single function in `search/`. If `search/normalize` ever gets renamed or moved (it is not documented in `search/AGENTS.md`), the content pipeline breaks silently. Concrete delta: move `canonicalize_url` to `utils/url_canonicalize.py` (or `utils/singleflight.py`-adjacent location) and have both `content/` and `search/` import from there.

3. **AGENTS.md is significantly out of date.** `rerank/AGENTS.md` documents three files that don't exist (`stack.py`, `policy.py`, `diversity.py`); `search/AGENTS.md` documents 10 files and the package has 30+ Python files (including `provider_catalog.py`, `provider_call.py`, `diagnostics.py`, `normalize.py`, `entity_extractor.py`, `gemini_search_tool.py`, `intents.py`, `merge_observability.py`, plus `academic/`, `understanding/` sub-packages). Root `AGENTS.md` link list omits `telemetry/`, `llm/`, `evals/`, `youtube/`, `config/`, `contracts/`, `dashboards/`. Concrete delta: regenerate per-package AGENTS.md from `git ls-files` (or a `ccc index` plus an LLM pass) and add a CI check that fails when a Python file in `src/` is not mentioned in its nearest AGENTS.md.

4. **Two MCP tool leaks + one orphan registration.** `composio_tools.py:163,176` and `analytics/tools.py:21,46` define `@mcp.tool(...)` outside `server.py`; `analytics/tools.py` is never called (its `register_analytics_tools` is not imported in `server.py` — verified). This is the only true layering violation in the MCP surface. The `tools/AGENTS.md` claim that "actual MCP tool implementations live in `server.py`" is therefore half-true. Concrete delta: either register `register_analytics_tools(mcp)` in `server.py` after `register_composio_tools(mcp)`, or delete `analytics/tools.py`. Also move the 2 composio tools into `server.py` to make the `tools/` package boundary actually hold.

5. **Hidden global state and async/sync mixing in `telemetry/`, `analytics/`, and `tools/_helpers.py`.** `telemetry/init.py` exposes `init_telemetry_background` (`server.py:27`) which sets module-level `_initialized` (`telemetry/constants.py:8-9`). `tools/_helpers.py:22` defines a module-level `_academic_search_flight = SingleFlight()`. `analytics/async_writes.py` uses a single-worker DuckDB executor (correctly documented in `analytics/AGENTS.md`). The mixing concern: `tools/_helpers.py` imports `asyncio.to_thread` for shutdown sequencing across HTTP client, telemetry, analytics, firecrawl, camoufox, and crawl4ai clients — six subsystems, one `__init__.py`-level shutdown pipeline. If any of those subsystems is added to the shutdown list out of order, partial-shutdown hangs. Concrete delta: replace the manual shutdown sequence in `tools/_helpers.py:11-18` with a single context-manager stack or a `lifecycle.register(...)` registry so each subsystem owns its own close.

## Concrete deltas summary (small, ordered)

1. Delete `tmp7k1_elq9.py`, `tmpzum42dot.py` (or move to `scripts/manual/`).
2. Decide: wire `register_analytics_tools(mcp)` in `server.py:102` or delete `analytics/tools.py`.
3. Remove `youtube/__init__.py:56` private re-export `_parse_iso8601_duration`.
4. Add `embeddings/__init__.py` re-export of `BatchLimitedEmbeddings` (currently claimed in AGENTS.md but missing).
5. Replace `telemetry/__init__.py` star-imports with explicit re-exports.
6. Move `search/normalize.canonicalize_url` to `utils/` and update 6 call sites in `content/`.
7. Add per-package `AGENTS.md` for `telemetry/`, `llm/`, `evals/`, `youtube/`, `config/`, `contracts/`.
8. Regenerate `search/AGENTS.md` and `rerank/AGENTS.md` against actual file lists; remove references to `stack.py`/`policy.py`/`diversity.py`.
9. Add `observability/__init__.py` (currently absent) or move `events.PERSISTED_EVENT_PREFIXES` to `utils/observability.py` directly.
10. Consolidate `dashboard/` and `dashboards/` (one is untracked, one is tracked; only one is canonical per `dashboards/app.py:1-9`).

---

ARCHITECTURE REVIEW DONE — Module layering holds (no cycles, clean direction), but `telemetry/` god-package, AGENTS.md drift in `search/` and `rerank/`, and one orphan `analytics/tools.py` MCP registration are the highest-impact fixes.
