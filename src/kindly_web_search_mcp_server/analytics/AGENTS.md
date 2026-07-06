# AGENTS.md - Analytics & Search Quality

This directory implements the DuckDB-backed analytics and evaluation layer.

## Current Structure

analytics/
|-- duckdb_store.py          # Storage schema and insert helpers
|-- rerank_candidate_writes.py # Batched rerank candidate survival inserts
|-- observability_schema.py  # Canonical observability schema definitions
|-- observability_tables.py  # Table builders for analytics storage
|-- observability_rows.py    # Row-shaping helpers for inserts
|-- observability_inserts.py # Insert helpers for the pipeline events
|-- observability_store.py   # Store facade for analytics writes
|-- observability_views.py   # Readable observability views
|-- candidate_views.py       # Candidate-focused views
|-- candidate_survival_views.py # Candidate survival analysis views
|-- derived_views.py         # Derived analytics views
|-- base_views.py            # Shared SQL view fragments
|-- views.py                 # Public analytics views
|-- queries.py               # Query helpers
|-- local_queries.py         # Local DuckDB query shortcuts
|-- reports.py               # Named analytics reports
|-- quality_metrics.py       # Run-level quality scoring
|-- summaries.py             # Daily aggregate refresh
|-- judge_prompt.py          # Judge prompt construction
|-- judge_runner.py          # Fire-and-forget judge evaluation
|-- judge_calibration.py     # Judge score normalization
|-- search_relevance_judge.py # Relevance-judge helpers
|-- evals.py                 # Evaluation helpers
|-- tools.py                 # Analytics utility helpers
└── motherduck_sync.py       # MotherDuck sync helpers

## Data Flow

All analytics rows join on `run_key`.

1. `search_runs` and query-understanding rows capture the request side
2. `provider_calls` and `provider_candidates` capture provider fanout
3. `merged_candidates` captures RRF output
4. `rerank_stages` and `rerank_candidates` capture reranking; candidate
   survival rows are batched per stage so analytics does not add per-row
   DuckDB connection overhead to the rerank hot path
5. `final_results` captures the public output
6. `search_quality_scores` stores computed quality metrics
7. `judge_evaluations` stores asynchronous judge results

## Current Behavior

- DuckDB is the source of truth for the analytics layer.
- Views exist for both human-friendly queries and programmatic reporting.
- Judge evaluation is fire-and-forget and should not block the response path.
- Report and query helpers should stay aligned with the underlying schema.

## Testing

- `python -m pytest tests/test_analytics_*.py`
- `python -m pytest tests/test_pipeline_tables.py tests/test_search_quality_scores.py`
