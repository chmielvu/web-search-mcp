<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-08-21 | Last verified: 2026-08-21 -->

# AGENTS.md - Analytics & Search Quality

DuckDB-backed analytics, quality metrics, LLM judge pipeline, and reports.

## Key Files

| File | Role |
|---|---|
| `duckdb_store.py` | Thin facade re-exporting writers + schema |
| `writers/schema.py` | DDL for fact tables, provider health, quality, judge, tool-call, and classifier events |
| `writers/core.py` | `TableWriter` + public insert wrappers |
| `writers/inserts.py` | Typed SQL insert statements for pipeline entities |
| `writers/table_names.py` | Canonical DuckDB table name definitions |
| `writers/connection.py` | `_db_path` + `_LOCK` + FlockMTL resources |
| `judges.py` | FlockMTL LLM-as-Judge orchestrator (6 facets) |
| `judge_calibration.py` | Cohen's κ calibration harness |
| `judge_runner.py` | Fire-and-forget judge evaluation |
| `quality_metrics.py` | Run-level quality scoring |
| `reports.py` | Named analytics reports, including provider reliability, quality misses, and classifier calibration |
| `views.py` | Dashboard, quality-diagnostic, calibration, A/B, and eval views |
| `summaries.py` | Daily aggregate refresh |
| `app.py` | Rich-based analytics UI |
| `motherduck_sync.py` | MotherDuck sync helpers |

## Data Flow

All analytics rows join on `run_key`. Pipeline tables:

1. `search_runs` — request side (query, intent, rewrite metadata [5 planner rewrites], timings)
2. `search_branches` — per-branch topology (6 fixed roles)
3. `provider_calls` — every outbound provider call
4. `search_candidates` — deduplicated RRF-scored candidates
5. `rerank_stages` + `rerank_candidates` — reranking stage results
6. `final_results` — public output with provider provenance
7. `query_embeddings` + `candidate_embeddings` — vector storage
8. `llm_call_log` — unified LLM cost tracking
9. `search_quality_scores` — computed quality metrics
10. `llm_judgments` — 6-facet FlockMTL judge verdicts (coverage max 5 rewrites)
11. `result_labels` — provenance-aware human/eval/model relevance annotations for offline replay
12. `tool_calls` — typed request/response/error lifecycle facts correlated by `tool_call_id`
13. `query_understanding_events` — classifier scores, decision paths, fallbacks, and outcome joins

## Branch-Role Model

Six fixed roles stored as `branch_role` on `search_branches` and `provider_calls`:
`original_free`, `paid_brave`, `paid_google`, `paid_other`, `neural`, `specialized`.

## Judge Pipeline (6 facets, two-stage inference chain)

- **Orchestrator**: `judges.py::judge_search_run(run_key)` + `schedule_judge_search_run(run_key)`
- **Inference chain** (HF router retired 2026-08-22): Stage 1 Gemini API
  `gemma-4-26b-a4b-it` via the native google-genai SDK (plain text; JSON
  recovered by the prompt footer + `_parse_result`) → Stage 2 NanoGPT
  `deepseek/deepseek-v4-flash-0731:thinking` with strict `json_schema`.
  Per-stage exponential backoff (3 attempts, 1s→8s cap, no jitter);
  non-retryable errors and empty completions fail over immediately;
  total exhaustion falls to the FlockMTL `llm_complete` last resort
  (its registry/secret point at NanoGPT).
- **Env keys**: `GEMINI_API_KEY` (stage 1), `NANOGPT_API_KEY` (stage 2 +
  fallback secret). `HF_TOKEN` is no longer consulted by judge code.
- **Facets**: `judge_run_overview` (1/run), `judge_intent_coherence` (1/run),
  `judge_rewrite_coverage` (1/run, 5 rewrite variants), `judge_rerank_improvement` (1/rerank_stages),
  `judge_result_quality` (1/final_result, ≤15), `judge_failure_cause` (1/run if failed)
- **Trigger**: fire-and-forget on a daemon `ThreadPoolExecutor(max_workers=4)`,
  wired into `search/outcomes.py::submit_search_outcome`
- **Cost guard**: `settings.flockmtl_enabled` (default true)
- **Judge-blindness**: banned reranker score names excluded from the SELECT
  whitelists and enumerated in prompts
- **Calibration note**: both aliases execute the identical chain; rebinding
  `_JUDGE_MODEL` changes provenance tagging only (`judge_calibration.py`).

## Rules

- DuckDB is disposable; recreated from fresh DDL.
- All persistence is non-blocking via `dispatch_duckdb_write` (single-worker executor).
- Hot-path collection is in-memory only.
- Judge evaluation never blocks the response path.
- `llm_call_log` is the unified source for per-call LLM cost attribution.
- `tool_calls` is the source of truth for MCP tool lifecycle analytics; legacy `search_events` persistence is not used.
- Provider diagnostics stay typed in `provider_calls` (`request_query`, `request_url`, `http_status`, `result_class`, `response_meta_json`).
- `result_labels` is offline-only; `source` distinguishes human, eval, and `llm_judge` annotations, and `discounted_gain` uses zero-based `label / log2(position + 2)`.
- Per-connection FlockMTL secret re-registration (`_ensure_flockmtl_secret`).
- Judge executor lifecycle is restartable: shutdown blocks scheduling only while the current executor is draining, then advances its generation and permits a fresh executor.

## Testing

```bash
uv run pytest tests/test_analytics_*.py
uv run pytest tests/test_pipeline_tables.py tests/test_search_quality_scores.py
uv run pytest tests/test_judges_facets.py tests/test_judge_after_outcome_write.py
uv run pytest tests/test_judge_chain.py tests/test_flockmtl_judge_routing.py
```
