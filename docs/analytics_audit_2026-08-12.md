# DuckDB Analytics Audit — Code-Path Findings

**Date:** 2026-08-12
**Source:** Three parallel code audits + 1 DuckDB inspection. Historical row-count data is *deliberately omitted* per the user's instruction. Every claim cites `file:line` of current code.
**Status of the original recommendations doc (`analytics_recommendations_2026-08-12.md`):** **superseded by this report.** The two cross-cutting corrections below invalidate several of the original "P0" and "P1" items.

---

## 0. Two cross-cutting corrections

The first pass made two claims that the code audits disprove:

1. **"5 MCP tools are uninstrumented"** — false. All 5 (`youtube_search`, `youtube_transcript`, `academic_search`, `grok_search`, `composio_similarlinks`) are instrumented via the same `emit_tool_observability_event` path that the 7 "working" tools use. The single sink is `src/kindly_web_search_mcp_server/utils/observability.py:340` (`_insert_tool_call_analytics`). Every entry function calls this for `request` / `response` / `error` phases. The 0-row count in `tool_calls` for those 5 tools is because they have not been exercised in the analytics window, not because they're unwired. (No alternative path exists — `grep` for `insert_tool_call_event` returns 4 files; only one production writer.)

2. **"`search_runs.tool_call_id` is broken"** — partially false. The writer path is **fully correct in current code** (`src/kindly_web_search_mcp_server/search/outcomes.py:82-87` → `service.py:57` → `tools/search.py:80,147`). The 99.6% NULL is historical data persisted before this cutover. New rows will be populated. The 0.4% non-null values in the live DB are the post-cutover window.

   Backfill is safe (one line):
   ```sql
   UPDATE search_runs SET tool_call_id = run_key WHERE tool_call_id IS NULL;
   ```
   because for both `web_search` MCP (`tools/search.py:146-147`) and the CLI (`cli/services/search_web.py:50-56`), `run_key == tool_call_id == str(uuid.uuid4())`.

The remaining findings (the empty tables, the missing `tool_calls.run_key`, and the missing `session_id` plumbing) **are** real and **are** the actual fix list. They're detailed below.

---

## 1. Per-table verdicts — the 22 empty/0-row tables

Every one is **ORPHAN** (DDL exists, no production caller). Grouped by subsystem.

### 1.1 `summary_*_daily` (4 tables) — REPAIRABLE ORPHAN

| Table | Verdict | Writer | DDL | Recommendation |
|---|---|---|---|---|
| `summary_intent_daily` | ORPHAN | `summaries.py:21` `refresh_summary_tables` | `writers/summary_schema.py:54` | **WIRE_IT** |
| `summary_provider_daily` | ORPHAN | same | `writers/summary_schema.py:34` | **WIRE_IT** |
| `summary_quality_daily` | ORPHAN | same | `writers/summary_schema.py:88` | **WIRE_IT** |
| `summary_rerank_daily` | ORPHAN | same | `writers/summary_schema.py:69` | **WIRE_IT** |

**Root cause:** `summaries.py:21` defines `refresh_summary_tables()` with the correct SQL, but no production code calls it. A whole-tree ripgrep excluding `tests/` returns exactly **one** match — the function's own definition. There's no startup hook, no APScheduler, no `asyncio.create_task`, no CLI command. DDL is created at server startup (`writers/schema.py:751-754`); the refresh is never executed.

**Fix (small):** add a single call in `server.py` startup:
```python
asyncio.create_task(periodic_refresh(settings.analytics_summary_interval_seconds))
```
or expose it as `web-search-cli analytics refresh` (the SQL is already there).

### 1.2 A/B testing (5 tables + 3 views) — DEAD SUBSYSTEM

| Table | Verdict | Writer | DDL |
|---|---|---|---|
| `ab_experiments` | ORPHAN | `writers/core.py:226` `insert_ab_experiment` (only called from `tests/test_ab_schema.py`) | `writers/ab_schema.py:35` |
| `ab_experiment_variants` | ORPHAN | **NO_WRITER** (no `insert_ab_variant` exists) | `writers/ab_schema.py:75` |
| `ab_assignments` | ORPHAN | **NO_WRITER** | `writers/ab_schema.py:90` |
| `ab_results` | ORPHAN | **NO_WRITER** | `writers/ab_schema.py:105` |
| `ab_shadow_runs` | ORPHAN | `writers/core.py:232` `insert_ab_shadow_run`, called from `ab_testing/shadow_runner.py:54` — `run_shadow` is only called from tests | `writers/ab_schema.py:55` |

Plus 3 views: `v_ab_experiment_summary`, `v_ab_variant_comparison`, `v_ab_shadow_run_analysis`.

**Root cause:** the whole `ab_testing/` package has DDL, writers, and a CLI (`cli/commands/experiments.py`), but **no pipeline integration**. `settings.ab_testing_enabled` defaults to `False`. Even when enabled, nothing in `src/kindly_web_search_mcp_server/search/` ever consults `get_ab_overrides`. `ab_experiment_variants`, `ab_assignments`, `ab_results` are **write-impossible by construction** (no writer function exists).

**Two of the 5 tables are write-impossible by construction.** This is unfinished work, not a config issue.

### 1.3 Eval harness (8 tables + 4 views) — DEAD SUBSYSTEM

| Table | Verdict | Writer | DDL |
|---|---|---|---|
| `eval_runs` | ORPHAN | **NO_WRITER** | `analytics/evals.py:15` |
| `eval_cases` | ORPHAN | **NO_WRITER** | `analytics/evals.py:27` |
| `eval_observations` | ORPHAN | **NO_WRITER** | `analytics/evals.py:42` |
| `eval_candidate_sets` | ORPHAN | **NO_WRITER** | `analytics/evals.py:80` |
| `eval_tool_calls` | ORPHAN | **NO_WRITER** | `analytics/evals.py:69` |
| `eval_scores` | ORPHAN | **NO_WRITER** | `analytics/evals.py:92` |
| `eval_judge_calls` | ORPHAN | **NO_WRITER** | `analytics/evals.py:104` |
| `eval_failures` | ORPHAN | **NO_WRITER** | `analytics/evals.py:116` |

Plus 4 views: `vw_eval_case_timeline`, `vw_eval_candidate_survival`, `vw_eval_provider_quality`, `vw_eval_pass_rate`.

**Root cause:** **there is no eval harness in this project.** No CLI subcommand, no programmatic entry point. The eval DDL is gated behind `ensure_eval_tables` (`evals.py:215`), which is only reachable from the orphan `motherduck_sync.sync_once`. All 8 tables are **write-impossible by construction** (no writer function exists). They were scaffolded for a future eval harness that was never built.

### 1.4 Judge calibration (2 tables)

| Table | Verdict | Writer | DDL |
|---|---|---|---|
| `judge_rubrics` | ORPHAN | `judge_calibration.py:441` (inside `run_calibration` at line 353) | `writers/schema.py:514` |
| `judge_calibration_set` | ORPHAN | **NO_WRITER** (table is only read from `judge_calibration.py:332-340`) | `writers/schema.py:532` |

**Root cause for `judge_rubrics`:** `run_calibration` is only reached from the `__main__` block at `judge_calibration.py:501` (`python -m kindly_web_search_mcp_server.analytics.judge_calibration --golden …`). That module is **not registered** in `cli/app.py:117-132`. No scheduled job, no startup hook.

**Root cause for `judge_calibration_set`:** this is the human-adjudicated ground truth table for Cohen's κ. By design, it's not seedable from production code. It exists to be filled by a human-in-the-loop calibration CLI. That CLI is not registered. Table is empty by intent, not by accident.

### 1.5 Provider health transitions

| Table | Verdict | Writer | DDL |
|---|---|---|---|
| `provider_health_transitions` | ORPHAN | `observability_inserts.py:53` `insert_provider_health_transition` | `writers/schema.py:415` |

**Root cause:** writer exists, but the actual circuit-breaker code in `src/kindly_web_search_mcp_server/telemetry/records_circuit.py:13,29` writes to **OpenTelemetry metrics** (`update_circuit_state`, `CIRCUIT_EVENT`, `CIRCUIT_FAILURE_THRESHOLD`) — not to this DuckDB table. No caller in `inference/`, `middleware/`, or `search/provider_registry.py` (only one match: a docstring constant at `provider_registry.py:56`).

**Fix (medium):** rewrite `telemetry/records_circuit.py` to also call `insert_provider_health_transition` for persistent history. This is the only way circuit-breaker analytics will be queryable from DuckDB.

### 1.6 Misc

| Table | Verdict | Writer | DDL | Note |
|---|---|---|---|---|
| `llm_quality_scores` | ORPHAN | **NO_WRITER** | `analytics/evals.py:56` | Not even in `writers/schema.py`. Only referenced by MotherDuck sync queries and read paths in `queries.py` / `reports.py`. |
| `analytics_sync_state` | ORPHAN | `motherduck_sync.py:230` inside `sync_once` (line 162) | `analytics/evals.py:127` | `sync_once` is re-exported in `analytics/__init__.py` but never called from production. `sync_loop` (`motherduck_sync.py:258`) is also never invoked. No scheduler. |
| `_hnsw_test` | ORPHAN | dev leak | — | 1-row table from a development session. Drop. |

---

## 2. The actual fix list (consolidated)

| # | File | Change | Why |
|---|---|---|---|
| 1 | `analytics/writers/schema.py:163-201` | Add `run_key VARCHAR` to `tool_calls` DDL + `CREATE INDEX idx_tool_calls_run_key` | Restore the join. |
| 2 | `analytics/writers/inserts.py:101-125` | Add `"run_key"` to `_TOOL_CALL_COLUMNS` (right after `"event_id"`) | Restore the join. |
| 3 | `utils/observability.py:365-395` | Inside `_insert_tool_call_analytics`, add `run_key=fields.get("run_key") or _current_run_key(),` to the `insert_tool_call_event(...)` kwargs. `_current_run_key` is from `inference/engine.py:32`, already bound by `tools/search.py:140` via `bind_run_context`. | Restore the join. |
| 4 | `tools/_helpers.py:53-64` | `_resolve_session_id`: add a final fallback to `middleware/session_tracking._FALLBACK_SESSION_ID` so it's never `None` for live MCP/CLI calls. | `session_id` 100% NULL on `search_runs`. |
| 5 | `utils/observability.py:368` | `session_id=fields.get("session_id") or _get_session_id_from_ctx(fields)` — put the fallback at the writer so the 12+ `emit_tool_observability_event` call sites don't need to change. | `session_id` 99% NULL on `tool_calls`. |
| 6 | `cli/services/search_web.py:50-56` | Add `session_id=run_key` (or a CLI-stable id) to the `execute_web_search(...)` call. | CLI path doesn't pass `session_id` at all. |
| 7 | `analytics/summaries.py` + `server.py` | Add a startup hook that calls `refresh_summary_tables()` once, then on a configurable interval (e.g., 5–15 min). | The 4 `summary_*_daily` tables are dead without this. |
| 8 | `utils/observability.py:396-397` | Change `logger.debug(...)` to `logger.warning(...)` for the silent-failure path. Today, every insert error is silently dropped. | Hidden errors are the biggest instrumentation risk for all 12 tools. |
| 9 | One-shot SQL | `UPDATE search_runs SET tool_call_id = run_key WHERE tool_call_id IS NULL;` | Backfill the 99.6% NULL from pre-cutover data. |

**Total diff: ~7 files, ~20 lines + the startup hook.** None of this changes the public MCP tool surface.

---

## 3. The join-repair diff (the user's Q3 answer: documented, not applied)

### 3.1 `tool_calls.run_key` — the omission

```diff
# src/kindly_web_search_mcp_server/analytics/writers/schema.py (line 163-201)
# Inside _ensure_tool_calls, after the recorded_at line:
         recorded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
         event_id             VARCHAR NOT NULL,
+        run_key              VARCHAR,
         tool_call_id         VARCHAR,
         session_id           VARCHAR,
         ...
+    connection.execute(
+        "CREATE INDEX IF NOT EXISTS idx_tool_calls_run_key ON tool_calls(run_key)"
+    )
```

```diff
# src/kindly_web_search_mcp_server/analytics/writers/inserts.py (line 101-125)
 _TOOL_CALL_COLUMNS = [
     "event_id",
+    "run_key",
     "tool_call_id",
     "session_id",
     ...
 ]
```

```diff
# src/kindly_web_search_mcp_server/utils/observability.py (line 365-395)
 # Inside _insert_tool_call_analytics, add the import at the top:
+from ..inference.engine import current_run_key as _current_run_key

 # Modify the insert_tool_call_event(...) call:
         insert_tool_call_event(
+            run_key=fields.get("run_key") or _current_run_key(),
             tool_call_id=fields.get("tool_call_id"),
             session_id=fields.get("session_id"),
             ...
         )
```

Why this works: `tools/search.py:140` already calls `bind_run_context(tool_call_id, operation="web_search")` for the LLM/OTEL contextvar. By reading the same contextvar at the analytics writer, every tool row that was emitted during a search run will pick up the right `run_key` automatically. The 5 non-search tools (`get_content`, `batch_get_content`, etc.) will get `None` — which is correct, since they're not part of a search run.

### 3.2 `search_runs.session_id` and `tool_calls.session_id` — the fallback

```diff
# src/kindly_web_search_mcp_server/tools/_helpers.py (line 53-64)
 def _resolve_session_id(ctx: Context | None) -> str | None:
     if ctx is None:
-        return None
+        from ..middleware.session_tracking import _FALLBACK_SESSION_ID
+        return _FALLBACK_SESSION_ID
     fastmcp_context = getattr(ctx, "fastmcp_context", None)
     ...
     return None  # ← also patch the final None-return
+    from ..middleware.session_tracking import _FALLBACK_SESSION_ID
+    return _FALLBACK_SESSION_ID
```

```diff
# src/kindly_web_search_mcp_server/utils/observability.py (line 368)
 # The session_id= line becomes:
-        session_id=fields.get("session_id"),
+        session_id=fields.get("session_id") or _get_session_id_from_ctx(fields),
```

where `_get_session_id_from_ctx(fields)` is a one-line helper that reads the request-scoped context (or returns `_FALLBACK_SESSION_ID` as a last resort).

```diff
# src/kindly_web_search_mcp_server/cli/services/search_web.py (line 50-56)
     response, run = await execute_web_search(
         request, http_client=await get_http_client(),
         run_key=run_key,
         tool_call_id=run_key,
+        session_id=run_key,
         return_diagnostics=True,
     )
```

### 3.3 Backfill for `search_runs.tool_call_id`

```sql
UPDATE search_runs SET tool_call_id = run_key WHERE tool_call_id IS NULL;
```

Safe because the production `web_search` MCP path and the CLI both use `run_key = tool_call_id = str(uuid.uuid4())`.

### 3.4 Why not apply this now

The diff is small, well-cited, and the failure mode of NOT applying it is silent (current behavior). The user's call: **document, don't apply.** When ready, the above is the exact patch.

---

## 4. Coverage matrix — MCP tools vs. analytics

| Tool | `tool_calls` writes? | `llm_call_log` writes? | joinable to `search_runs` after fix? | Notes |
|---|:-:|:-:|:-:|---|
| `web_search` | ✅ (1409 rows) | ✅ via rewrite | ✅ | reference path |
| `quick_web_search` | ✅ (120) | partial | ✅ | |
| `get_content` | ✅ (695) | ❌ | n/a | pure fetch |
| `batch_get_content` | ✅ (165) | ❌ | n/a | pure fetch |
| `discover_links` | ✅ (33) | ❌ | n/a | pure fetch |
| `gemini_search` | ✅ (36) | ❌ | n/a | LLM-based but no cost row |
| `generate_sitemap` | ✅ (1) | ❌ | n/a | rare; no Tavily crawl cost tracked |
| `youtube_search` | ✅ INSTRUMENTED (0 rows so far) | n/a | n/a | unwired per agent #1 |
| `youtube_transcript` | ✅ INSTRUMENTED (0 rows) | n/a | n/a | unwired per agent #1 |
| `academic_search` | ✅ INSTRUMENTED (0 rows) | n/a | n/a | unwired per agent #1 |
| `grok_search` | ✅ INSTRUMENTED (0 rows) | ❌ | n/a | LLM cost not tracked |
| `composio_similarlinks` | ✅ INSTRUMENTED (0 rows) | n/a | n/a | unwired per agent #1 |

The "✅ INSTRUMENTED (0 rows)" cells were wrong in the original report. The instrument is there. The cause of the 0 rows is no calls in the window, not a missing writer.

---

## 5. Recommended action checklist

**Immediate (no code change):**
- [ ] `UPDATE search_runs SET tool_call_id = run_key WHERE tool_call_id IS NULL;` (backfill, safe)
- [ ] Per-table decision on the 22 orphan tables (asked in this turn's follow-up questionnaire)

**Small (≤1 day):**
- [ ] Apply the 7-file join-repair diff from §3 (held per user's answer)
- [ ] Add `refresh_summary_tables()` call in `server.py` startup (wired with a configurable interval)
- [ ] Promote `utils/observability.py:396-397` `logger.debug` to `logger.warning` to expose silent failures

**Medium (1–2 days):**
- [ ] Wire `provider_health_transitions` from `telemetry/records_circuit.py` (only meaningful if you also use the OTel path)
- [ ] Either commit to building the eval harness or drop the 8 `eval_*` tables
- [ ] Either commit to wiring the A/B framework or drop the 5 `ab_*` tables and 3 `v_ab_*` views

**Large (backlog):**
- [ ] Eval harness: `web-search-cli eval run <suite>` command, plus the writer code
- [ ] A/B framework: `get_ab_overrides` integration into `search/`, plus a wiring story
- [ ] Judge calibration: register `judge-calibration` as a CLI subcommand, schedule it

---

## 6. Open questions / per-table decisions for the user

Pending your choices on the 22 orphan tables. See the questionnaire in the next assistant turn.

---

*This report supersedes `docs/analytics_recommendations_2026-08-12.md`. The original is kept for history but should not be used as a work plan.*
