"""TableWriter instances for every analytics table."""

from __future__ import annotations

from .core import TableWriter
from .table_names import (
    _ABE_TABLE_NAME,
    _ABS_TABLE_NAME,
    _CE_TABLE_NAME,
    _FR_TABLE_NAME,
    _JE_TABLE_NAME,
    _PC_TABLE_NAME,
    _QE_TABLE_NAME,
    _RC_TABLE_NAME,
    _RS_TABLE_NAME,
    _RUNS_TABLE_NAME,
    _SB_TABLE_NAME,
    _SC_TABLE_NAME,
    _SQS_TABLE_NAME,
)

# ---------------------------------------------------------------------------
# Column lists (must match DDL in schema.py)
# ---------------------------------------------------------------------------
_SEARCH_RUN_COLUMNS = [
    "run_key",
    "tool_call_id",
    "session_id",
    "query",
    "normalized_query",
    "research_goal",
    "intent",
    "understanding_confidence",
    "num_results_requested",
    "rewrite_enabled",
    "selected_providers",
    "skipped_providers",
    "branch_count",
    "provider_count",
    "merged_count",
    "reranked_count",
    "final_result_count",
    "candidate_count",
    "status",
    "error_type",
    "duration_ms",
    "reranker_provider",
    "reranker_model",
    "rake_terms",
    "brave_autosuggest",
    "brave_spellcheck",
    "rewrite_prompt",
    "rewrite_model",
    "rewrite_input_tokens",
    "rewrite_output_tokens",
    "rewrite_latency_ms",
    "rewrite_error",
    "payload_json",
]

_SEARCH_BRANCH_COLUMNS = [
    "run_key",
    "branch_index",
    "branch_role",
    "branch_query",
    "branch_why",
    "support_terms",
    "max_results",
    "assigned_providers",
    "attempted_providers",
    "skipped_providers",
    "results_count",
    "latency_ms",
    "payload_json",
]

_PROVIDER_CALL_COLUMNS = [
    "run_key",
    "branch_index",
    "branch_role",
    "provider",
    "branch_query",
    "status",
    "num_results_requested",
    "num_results_returned",
    "latency_ms",
    "error_type",
    "error_message",
    "candidate_urls",
    "payload_json",
]

_SEARCH_CANDIDATE_COLUMNS = [
    "run_key",
    "link",
    "title",
    "snippet",
    "domain",
    "rrf_score",
    "provider_count",
    "providers",
    "overlap_flag",
    "payload_json",
]

_RERANK_STAGE_COLUMNS = [
    "run_key",
    "stage",
    "provider",
    "model",
    "input_count",
    "output_count",
    "duration_ms",
    "max_score",
    "avg_score",
    "score_threshold",
    "alpha_blend",
    "input_tokens",
    "output_tokens",
    "status",
    "error_type",
    "instruction_present",
    "instruction_length",
    "query_type_hint",
    "entity_overlap_enabled",
    "payload_json",
]

_RERANK_CANDIDATE_COLUMNS = [
    "run_key",
    "stage",
    "link",
    "candidate_id",
    "canonical_result_id",
    "rank_before",
    "rank_after",
    "score_before",
    "score_after",
    "bm25_score",
    "bm25_rank",
    "dense_score",
    "dense_rank",
    "cross_encoder_raw",
    "llm_raw_score",
    "fused_score",
    "hybrid_rrf_score",
    "recency_boost",
    "entity_overlap_score",
    "survived",
    "payload_json",
]

_FINAL_RESULT_COLUMNS = [
    "run_key",
    "rank",
    "title",
    "link",
    "snippet",
    "domain",
    "final_score",
    "providers",
    "provider_count",
    "entities_count",
    "candidate_id",
    "canonical_result_id",
    "payload_json",
]

_QUERY_EMBEDDING_COLUMNS = [
    "run_key",
    "embedding",
    "model_id",
    "payload_json",
]

_CANDIDATE_EMBEDDING_COLUMNS = [
    "run_key",
    "link",
    "title",
    "embedding",
    "model_id",
    "payload_json",
]

_SEARCH_QUALITY_SCORE_COLUMNS = [
    "run_key",
    "provider_overlap_rate",
    "domain_diversity_count",
    "domain_diversity_ratio",
    "rerank_compression_ratio",
    "avg_rrf_score",
    "top_score",
    "p95_score",
    "provider_count",
    "branch_count",
    "total_candidates_input",
    "total_candidates_merged",
    "total_candidates_reranked",
    "total_final_results",
    "ndcg_at_10",
    "payload_json",
]

_JUDGE_EVALUATION_COLUMNS = [
    "run_key",
    "tool_name",
    "judge_model",
    "model_used",
    "link",
    "relevance_grade",
    "relevance_score",
    "accuracy_grade",
    "accuracy_score",
    "completeness_grade",
    "completeness_score",
    "source_quality_grade",
    "source_quality_score",
    "overall_score",
    "rationale",
    "duration_ms",
    "input_tokens",
    "output_tokens",
    "tokens_used",
    "cost_usd",
    "payload_json",
]

_AB_EXPERIMENT_COLUMNS = [
    "experiment_id",
    "layer",
    "variant_a",
    "variant_b",
    "allocation_rate",
    "status",
    "start_date",
    "end_date",
    "min_sample_size",
    "payload_json",
]

_AB_SHADOW_RUN_COLUMNS = [
    "run_key",
    "experiment_id",
    "variant",
    "layer",
    "duration_ms",
    "judge_score",
    "tokens_used",
    "cost_usd",
    "error_type",
    "payload_json",
]


# ---------------------------------------------------------------------------
# TableWriter instances
# ---------------------------------------------------------------------------
_SEARCH_RUN_WRITER = TableWriter(
    table_name=_RUNS_TABLE_NAME,
    ensure_name="_ensure_search_runs",
    columns=_SEARCH_RUN_COLUMNS,
    task_name="analytics.search_run",
)
_SEARCH_BRANCHES_WRITER = TableWriter(
    table_name=_SB_TABLE_NAME,
    ensure_name="_ensure_search_branches",
    columns=_SEARCH_BRANCH_COLUMNS,
    task_name="analytics.search_branches",
)
_PROVIDER_CALLS_WRITER = TableWriter(
    table_name=_PC_TABLE_NAME,
    ensure_name="_ensure_provider_calls",
    columns=_PROVIDER_CALL_COLUMNS,
    task_name="analytics.provider_calls",
)
_SEARCH_CANDIDATES_WRITER = TableWriter(
    table_name=_SC_TABLE_NAME,
    ensure_name="_ensure_search_candidates",
    columns=_SEARCH_CANDIDATE_COLUMNS,
    task_name="analytics.search_candidates",
)
_RERANK_STAGES_WRITER = TableWriter(
    table_name=_RS_TABLE_NAME,
    ensure_name="_ensure_rerank_stages",
    columns=_RERANK_STAGE_COLUMNS,
    task_name="analytics.rerank_stages",
)
_RERANK_CANDIDATES_WRITER = TableWriter(
    table_name=_RC_TABLE_NAME,
    ensure_name="_ensure_rerank_candidates",
    columns=_RERANK_CANDIDATE_COLUMNS,
    task_name="analytics.rerank_candidates",
)
_FINAL_RESULTS_WRITER = TableWriter(
    table_name=_FR_TABLE_NAME,
    ensure_name="_ensure_final_results",
    columns=_FINAL_RESULT_COLUMNS,
    task_name="analytics.final_results",
)
_QUERY_EMBEDDINGS_WRITER = TableWriter(
    table_name=_QE_TABLE_NAME,
    ensure_name="_ensure_query_embeddings",
    columns=_QUERY_EMBEDDING_COLUMNS,
    task_name="analytics.query_embeddings",
)
_CANDIDATE_EMBEDDINGS_WRITER = TableWriter(
    table_name=_CE_TABLE_NAME,
    ensure_name="_ensure_candidate_embeddings",
    columns=_CANDIDATE_EMBEDDING_COLUMNS,
    task_name="analytics.candidate_embeddings",
)
_SEARCH_QUALITY_SCORES_WRITER = TableWriter(
    table_name=_SQS_TABLE_NAME,
    ensure_name="_ensure_search_quality_scores",
    columns=_SEARCH_QUALITY_SCORE_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.search_quality_scores",
)
_JUDGE_EVALUATION_WRITER = TableWriter(
    table_name=_JE_TABLE_NAME,
    ensure_name="_ensure_judge_evaluations",
    columns=_JUDGE_EVALUATION_COLUMNS,
    task_name="analytics.judge_evaluation",
)
_AB_EXPERIMENT_WRITER = TableWriter(
    table_name=_ABE_TABLE_NAME,
    ensure_name="_ensure_ab_experiments",
    columns=_AB_EXPERIMENT_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.ab_experiment",
)
_AB_SHADOW_RUN_WRITER = TableWriter(
    table_name=_ABS_TABLE_NAME,
    ensure_name="_ensure_ab_shadow_runs",
    columns=_AB_SHADOW_RUN_COLUMNS,
    task_name="analytics.ab_shadow_run",
)
