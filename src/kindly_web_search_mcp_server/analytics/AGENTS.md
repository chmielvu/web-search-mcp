# AGENTS.md - Analytics & Search Quality

This directory implements the search quality analytics pipeline with DuckDB storage.

## Structure

analytics/
|-- duckdb_store.py          # 21 DuckDB tables + insert functions for search quality pipeline
|-- views.py                 # 13 human-readable SQL views for analytics queries
|-- quality_metrics.py       # compute_search_quality() per-run quality scoring
|-- summaries.py             # refresh_summary_tables() daily aggregate refresh
|-- judge_prompt.py          # LLM judge prompt construction and score parsing
|-- judge_runner.py          # Fire-and-forget LLM judge evaluation after production response
-- judge_calibration.py     # Judge score normalization/calibration

## Pipeline Data Flow (all joined by run_key)

1. search_runs -> query_understanding -> query_rewrites (input side)
2. provider_calls -> provider_candidates (per-provider results)
3. merged_candidates (RRF merge output)
4. rerank_stages -> rerank_candidates (multi-stage reranking)
5. final_results (output)
6. search_quality_scores (computed quality metrics)
7. judge_evaluations (LLM-as-judge scoring)

## Key Components

### DuckDB Schema (21 tables)
See docs/DuckDB_schema.md for full reference.

### Quality Metrics
- compute_search_quality() scores each search run
- Metrics include precision, recall, latency, provider diversity

### LLM Judge
- judge_runner.py runs fire-and-forget evaluation after production response
- judge_prompt.py constructs prompts for judge model
- judge_calibration.py normalizes judge scores

## Testing
pytest tests/test_analytics_query_cli_prints_json.py tests/test_analytics_report_cli_prints_json.py -v

## Conventions
- All tables joined by run_key for traceability
- Views provide human-readable query interfaces
- Judge evaluation is async and non-blocking
