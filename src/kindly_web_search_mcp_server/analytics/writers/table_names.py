"""DuckDB table-name constants for the analytics writers."""

from __future__ import annotations

# Primary pipeline tables (7 wide fact tables + 2 embedding tables)
_RUNS_TABLE_NAME = "search_runs"
_SB_TABLE_NAME = "search_branches"
_PC_TABLE_NAME = "provider_calls"
_SC_TABLE_NAME = "search_candidates"
_RS_TABLE_NAME = "rerank_stages"
_RC_TABLE_NAME = "rerank_candidates"
_FR_TABLE_NAME = "final_results"
_QE_TABLE_NAME = "query_embeddings"
_CE_TABLE_NAME = "candidate_embeddings"

# Provider health (moved from observability_schema.py — different grain)
_PH_TABLE_NAME = "provider_health_transitions"

# Quality / judge tables
_SQS_TABLE_NAME = "search_quality_scores"
_JE_TABLE_NAME = "judge_evaluations"

# Daily summary tables
_SUM_PVD_TABLE_NAME = "summary_provider_daily"
_SUM_ID_TABLE_NAME = "summary_intent_daily"
_SUM_RD_TABLE_NAME = "summary_rerank_daily"
_SUM_QD_TABLE_NAME = "summary_quality_daily"

# A/B experiment tables
_ABE_TABLE_NAME = "ab_experiments"
_ABS_TABLE_NAME = "ab_shadow_runs"
_ABV_TABLE_NAME = "ab_experiment_variants"
_ABA_TABLE_NAME = "ab_assignments"
_ABR_TABLE_NAME = "ab_results"

__all__ = [
    "_RUNS_TABLE_NAME",
    "_SB_TABLE_NAME",
    "_PC_TABLE_NAME",
    "_SC_TABLE_NAME",
    "_RS_TABLE_NAME",
    "_RC_TABLE_NAME",
    "_FR_TABLE_NAME",
    "_QE_TABLE_NAME",
    "_CE_TABLE_NAME",
    "_PH_TABLE_NAME",
    "_SQS_TABLE_NAME",
    "_JE_TABLE_NAME",
    "_SUM_PVD_TABLE_NAME",
    "_SUM_ID_TABLE_NAME",
    "_SUM_RD_TABLE_NAME",
    "_SUM_QD_TABLE_NAME",
    "_ABE_TABLE_NAME",
    "_ABS_TABLE_NAME",
    "_ABV_TABLE_NAME",
    "_ABA_TABLE_NAME",
    "_ABR_TABLE_NAME",
]
