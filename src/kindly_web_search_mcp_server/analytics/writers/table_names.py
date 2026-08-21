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

# LLM call log
_LLM_CALL_LOG_TABLE_NAME = "llm_call_log"

# Typed observability facts
_TC_TABLE_NAME = "tool_calls"
_QUE_TABLE_NAME = "query_understanding_events"

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

# Quick web search tables
_QWSR_TABLE_NAME = "quick_web_search_runs"
_QWSC_TABLE_NAME = "quick_web_search_citations"

# Gemini search tables
_GSR_TABLE_NAME = "gemini_search_runs"
_GSS_TABLE_NAME = "gemini_search_sources"
_GSA_TABLE_NAME = "gemini_search_attempts"

# Code search tables
_CSR_TABLE_NAME = "code_search_runs"
_CSP_TABLE_NAME = "code_search_providers"
_CSD_TABLE_NAME = "code_search_diagnostics"
_CSH_TABLE_NAME = "code_search_hits"
_CSHV_TABLE_NAME = "code_search_hit_variants"
_CSQV_TABLE_NAME = "code_search_query_variants"
_CSREPO_TABLE_NAME = "code_search_repositories"
_CSRERANK_TABLE_NAME = "code_search_rerank"

# Content operations and summary tables
_CO_TABLE_NAME = "content_operations"
_CF_TABLE_NAME = "content_fetches"
_CSUM_TABLE_NAME = "content_summaries"
_CSUMA_TABLE_NAME = "content_summary_attempts"

# Web search funnel uplift tables
_RC_CAT_TABLE_NAME = "result_catalog"
_PR_TABLE_NAME = "provider_results"
_QV_TABLE_NAME = "query_variants"
_QT_TABLE_NAME = "query_transforms"
_CSE_TABLE_NAME = "candidate_stage_events"
_TOI_TABLE_NAME = "tool_output_items"

# Result labels foundation
_RL_TABLE_NAME = "result_labels"
_RESULT_LABELS_TABLE_NAME = "result_labels"

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
    "_LLM_CALL_LOG_TABLE_NAME",
    "_TC_TABLE_NAME",
    "_QUE_TABLE_NAME",
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
    "_QWSR_TABLE_NAME",
    "_QWSC_TABLE_NAME",
    "_GSR_TABLE_NAME",
    "_GSS_TABLE_NAME",
    "_GSA_TABLE_NAME",
    "_CSR_TABLE_NAME",
    "_CSP_TABLE_NAME",
    "_CSD_TABLE_NAME",
    "_CSH_TABLE_NAME",
    "_CSHV_TABLE_NAME",
    "_CSQV_TABLE_NAME",
    "_CSREPO_TABLE_NAME",
    "_CSRERANK_TABLE_NAME",
    "_CO_TABLE_NAME",
    "_CF_TABLE_NAME",
    "_CSUM_TABLE_NAME",
    "_CSUMA_TABLE_NAME",
    "_RC_CAT_TABLE_NAME",
    "_PR_TABLE_NAME",
    "_QV_TABLE_NAME",
    "_QT_TABLE_NAME",
    "_CSE_TABLE_NAME",
    "_TOI_TABLE_NAME",
    "_RL_TABLE_NAME",
    "_RESULT_LABELS_TABLE_NAME",
]
