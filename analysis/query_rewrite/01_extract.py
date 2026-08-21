#!/usr/bin/env python3
"""Extract the core tables needed for query-rewrite analysis.

Outputs:
  artifacts/search_runs.parquet
  artifacts/search_branches.parquet
  artifacts/final_results.parquet
  artifacts/search_candidates.parquet
  artifacts/run_branch_pairs.parquet
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

DB = Path(
    r"C:\Users\Jan\Documents\GitHub\1Agents1\.CLI\web-search-mcp"
    r"\duckdb_data\analytics\search_events.duckdb"
)
OUT = Path(__file__).parent / "artifacts"
OUT.mkdir(parents=True, exist_ok=True)

con = duckdb.connect(str(DB), read_only=True)

# Core tables
runs = con.execute(
    """
    SELECT
        run_key,
        recorded_at,
        query,
        normalized_query,
        research_goal,
        intent,
        understanding_confidence,
        num_results_requested,
        rewrite_enabled,
        rewrite_model,
        rewrite_latency_ms,
        rewrite_input_tokens,
        rewrite_output_tokens,
        rewrite_error,
        selected_providers,
        skipped_providers,
        branch_count,
        provider_count,
        merged_count,
        reranked_count,
        final_result_count,
        candidate_count,
        status,
        error_type,
        duration_ms,
        reranker_provider,
        reranker_model
    FROM search_runs
    """
).fetch_df()

branches = con.execute(
    """
    SELECT
        run_key,
        branch_index,
        branch_role,
        branch_query,
        branch_why,
        support_terms,
        max_results,
        assigned_providers,
        attempted_providers,
        skipped_providers,
        results_count,
        latency_ms,
        branch_id
    FROM search_branches
    """
).fetch_df()

finals = con.execute(
    """
    SELECT
        run_key,
        rank,
        title,
        link,
        domain,
        final_score,
        providers,
        provider_count,
        entities_count,
        candidate_id,
        canonical_result_id
    FROM final_results
    """
).fetch_df()

cands = con.execute(
    """
    SELECT
        run_key,
        link,
        title,
        snippet,
        domain,
        rrf_score,
        provider_count,
        providers,
        overlap_flag,
        canonical_result_id
    FROM search_candidates
    """
).fetch_df()

# Pair-level table: input query <-> rewrite per branch
pairs = con.execute(
    """
    SELECT
        r.run_key,
        r.intent,
        r.status,
        r.rewrite_enabled,
        r.rewrite_model,
        r.rewrite_error,
        r.candidate_count,
        r.final_result_count,
        r.understanding_confidence,
        b.branch_index,
        b.branch_role,
        b.branch_query,
        b.branch_why,
        b.assigned_providers,
        b.attempted_providers,
        b.results_count,
        b.latency_ms,
        r.query AS input_query
    FROM search_runs r
    JOIN search_branches b USING (run_key)
    """
).fetch_df()

runs.to_parquet(OUT / "search_runs.parquet", index=False)
branches.to_parquet(OUT / "search_branches.parquet", index=False)
finals.to_parquet(OUT / "final_results.parquet", index=False)
cands.to_parquet(OUT / "search_candidates.parquet", index=False)
pairs.to_parquet(OUT / "run_branch_pairs.parquet", index=False)

print("Extracted:")
print(f"  search_runs:          {len(runs):>6}")
print(f"  search_branches:      {len(branches):>6}")
print(f"  final_results:        {len(finals):>6}")
print(f"  search_candidates:    {len(cands):>6}")
print(f"  run_branch_pairs:     {len(pairs):>6}")
print(f"  unique runs:          {pairs['run_key'].nunique():>6}")
print(f"  unique input queries: {pairs['input_query'].nunique():>6}")
print(f"  branch_role counts:\n{pairs['branch_role'].value_counts().to_string()}")
con.close()
