# AGENTS.md - Analytics & Search Quality

DuckDB-backed analytics, quality metrics, LLM judge pipeline, and reports.

## Key Files

| File | Role |
|---|---|
| `duckdb_store.py` | Thin facade re-exporting writers + schema |
| `writers/schema.py` | DDL for 31+ tables (fact tables, provider health, quality, judge) |
| `writers/core.py` | `TableWriter` + public insert wrappers |
| `writers/connection.py` | `_db_path` + `_LOCK` + FlockMTL resources |
| `async_writes.py` | Single-worker DuckDB write executor |
| `judges.py` | FlockMTL LLM-as-Judge orchestrator (6 facets) |
| `judge_calibration.py` | Cohen's κ calibration harness |
| `judge_runner.py` | Fire-and-forget judge evaluation |
| `quality_metrics.py` | Run-level quality scoring |
| `reports.py` | Named analytics reports |
| `views.py` | 13 dashboard views + A/B + eval views |
| `summaries.py` | Daily aggregate refresh |
| `app.py` | Rich-based analytics UI |
| `motherduck_sync.py` | MotherDuck sync helpers |

## Data Flow

All analytics rows join on `run_key`. Pipeline tables:

1. `search_runs` — request side (query, intent, rewrite metadata, timings)
2. `search_branches` — per-branch topology (6 fixed roles)
3. `provider_calls` — every outbound provider call
4. `search_candidates` — deduplicated RRF-scored candidates
5. `rerank_stages` + `rerank_candidates` — reranking stage results
6. `final_results` — public output with provider provenance
7. `query_embeddings` + `candidate_embeddings` — vector storage
8. `llm_call_log` — unified LLM cost tracking
9. `search_quality_scores` — computed quality metrics
10. `llm_judgments` — 6-facet FlockMTL judge verdicts

## Branch-Role Model

Six fixed roles stored as `branch_role` on `search_branches` and `provider_calls`:
`original_free`, `paid_brave`, `paid_google`, `paid_other`, `neural`, `specialized`.

## FlockMTL Judge Pipeline (6 facets)

- **Orchestrator**: `judges.py::judge_search_run(run_key)` + `schedule_judge_search_run(run_key)`
- **Facets**: `judge_run_overview` (1/run), `judge_intent_coherence` (1/run),
  `judge_rewrite_coverage` (1/run), `judge_rerank_improvement` (1/rerank_stages),
  `judge_result_quality` (1/final_result, ≤15), `judge_failure_cause` (1/run if failed)
- **Trigger**: `schedule_judge_search_run` is fire-and-forget on `ThreadPoolExecutor(max_workers=4)`,
  wired into `search/outcomes.py::submit_search_outcome`
- **Cost guard**: `settings.flockmtl_enabled` (default true)
- **Judge-blindness**: Banned score names (`final_score`, `llm_raw_score`, etc.)
  are excluded from the SELECT whitelist and enumerated in prompts

## Rules

- DuckDB is disposable; recreated from fresh DDL.
- All persistence is non-blocking via `dispatch_duckdb_write` (single-worker executor).
- Hot-path collection is in-memory only.
- Judge evaluation never blocks the response path.
- `llm_call_log` is the unified source for per-call LLM cost attribution.
- Per-connection FlockMTL secret re-registration (`_ensure_flockmtl_secret`).

## Testing

```bash
uv run pytest tests/test_analytics_*.py
uv run pytest tests/test_pipeline_tables.py tests/test_search_quality_scores.py
uv run pytest tests/test_judges_facets.py tests/test_judge_after_outcome_write.py
```
