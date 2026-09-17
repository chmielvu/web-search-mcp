<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-09-16 | Last verified: 2026-09-16 -->

# AGENTS.md - Analytics & Search Quality

DuckDB-backed analytics, quality metrics, LLM judge pipeline, and reports.

## Key Files

| File | Role |
|---|---|
| `ids.py` | Stable result-ID/candidate-ID hash helpers (`_canonical_result_id`, `_candidate_id`) |
| `events.py` | `PERSISTED_EVENT_PREFIXES` — persisted event-family allowlist (absorbed from top-level `observability/`) |
| `writers/schema.py` | DDL for fact tables, quality, judge, tool-call, and classifier events |
| `writers/core.py` | `TableWriter` + public insert wrappers |
| `writers/inserts.py` | Typed SQL insert statements for pipeline entities |
| `writers/table_names.py` | Canonical DuckDB table name definitions |
| `writers/connection.py` | `_db_path` + `_LOCK` + FlockMTL resources |
| `judges/` | FlockMTL LLM-as-Judge orchestrator (6 facets): `run.py` (`judge_search_run`, `schedule_judge_search_run`), `executor.py` (daemon pool lifecycle), `stages.py` (stage calling + parsing), `digest.py` (run digest), `jobs.py` (parallel-facet primitives), `persistence.py` (judgment rows) |
| `judge_runner.py` | Fire-and-forget judge evaluation |
| `quality_metrics.py` | Run-level quality scoring |
| `reports.py` | Named analytics reports, including provider reliability, quality misses, and classifier calibration |
| `views/` | Dashboard, funnel-uplift, and fetch-observability view bootstrap: `dashboard_sql.py` / `funnel_sql.py` / `fetch_observability_sql.py` hold the SQL, `__init__.py` orchestrates |
| `embedding_sql.py` | Shared SQL/bootstrap for `vw_embedding_similarity` |
| `producers/` | Observability event persistence for tool calls and content operations (`emit_observability_event`, `emit_tool_observability_event`) |
| `motherduck_sync.py` | MotherDuck sync helpers |
| `graph_feedback.py` | Direct read-only DuckDB observation query, in-memory NetworkX graph computation, and `generate`/`compare` SQLite operations |
| `graph_store.py` | SQLite WAL persistence, transactional generation publication, ready-generation loading, and path-scoped cache |
| `graph_replay.py` | Read-only DuckDB run-history replay against SQLite graph artifacts plus control/treatment metrics |
| `training/` | Write-only query-understanding JSONL sink + TTL session state (moved from top-level `training/`) |
| `evals/` | Eval case models, deterministic metrics, offline strict-JSON judges (moved from top-level `evals/`; mcpevals runner deleted — no consumers) |

## Data Flow

All analytics rows join on `run_key`. Pipeline tables:

1. `search_runs` — request side (query, intent, rewrite metadata [5 planner rewrites], timings)
2. `search_branches` — per-branch topology (6 fixed roles)
3. `provider_calls` — every outbound provider call
4. `search_candidates` — deduplicated RRF-scored candidates
5. `rerank_stages` + `rerank_candidates` — ordered rerank stages and canonical score facts
   (`bi_encoder`, `cross_encoder`, `rankllm`, with terminal `mmr_fallback` on fallback)
6. `final_results` — public output with provider provenance
7. `query_embeddings` + `candidate_embeddings` — vector storage
8. `llm_call_log` — unified LLM cost tracking
9. `search_quality_scores` — computed quality metrics
10. `llm_judgments` — 6-facet FlockMTL judge verdicts (coverage max 5 rewrites)
11. `result_labels` — provenance-aware human/eval/model relevance annotations for offline replay
12. `tool_calls` — typed request/response/error lifecycle facts correlated by `tool_call_id`
13. `query_understanding_events` — classifier scores, decision paths, fallbacks, and outcome joins
14. SQLite graph artifact — generation manifests, Adamic-Adar neighbors, and document-side BiRank/PageRank features
- Rerank candidate facts use `final_score_before`/`after`, `bm25_*`,
  `bi_encoder_*`, `cross_encoder_score`, `rankllm_score`,
  `retrieval_rrf_score`, `recency_score`, `diversity_penalty`, and survival flags;
  historical legacy score columns are migration-only and receive no new writes.
- Graph topology uses all time-windowed `final_results` query/document observations; judged `result_labels` remain the supervised edge-weight source. Related-query support is exposure co-occurrence, while BiRank/PageRank features remain judge-weighted.

## Branch-Role Model

Six fixed roles stored as `branch_role` on `search_branches` and `provider_calls`:
`original`, `free`, `serp1`, `serp2`, `semantic_tavily`, `semantic_exa`.

## Judge Pipeline (6 facets, two-stage inference chain)

- **Orchestrator**: `judges/` package — `judge_search_run(run_key)` + `schedule_judge_search_run(run_key)`
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

## Rules

- DuckDB is disposable; recreated from fresh DDL.
- All persistence is non-blocking via `dispatch_duckdb_write` (single-worker executor).
- Hot-path collection is in-memory only.
- Judge evaluation never blocks the response path.
- `llm_call_log` is the unified source for per-call LLM cost attribution.
- `tool_calls` is the source of truth for MCP tool lifecycle analytics; legacy `search_events` persistence is not used.
- Provider diagnostics stay typed in `provider_calls` (`request_query`, `request_url`, `http_status`, `result_class`, `response_meta_json`).
- `result_labels` is offline-only; `source` distinguishes human, eval, and `llm_judge` annotations, and `discounted_gain` uses zero-based `label / log2(position + 2)`. The table, DDL, and writers (`insert_result_labels`, `upsert_materialized_result_labels`) exist, but nothing populates them yet: the `llm_judge` materializer was removed as dead code, so a producer still has to be wired before the graph-feedback replay sees labels.
- Per-connection FlockMTL secret re-registration (`_ensure_flockmtl_secret`).
- Judge executor lifecycle is restartable: shutdown blocks scheduling only while the current executor is draining, then advances its generation and permits a fresh executor.

## Testing

The `tests/` suite is frozen and removed from the repository by project policy — do not add, run, or modify tests. Verify analytics changes with:

```bash
uv run ruff check src/
uv run python -c "import kindly_web_search_mcp_server.analytics.app; print('analytics imports OK')"
uv run web-search-cli doctor
```
