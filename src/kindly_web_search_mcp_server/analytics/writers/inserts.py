"""TableWriter instances for every analytics table."""

from __future__ import annotations

from .core import TableWriter
from .table_names import (
    _ABE_TABLE_NAME,
    _ABS_TABLE_NAME,
    _FR_TABLE_NAME,
    _JE_TABLE_NAME,
    _MC_TABLE_NAME,
    _PC_TABLE_NAME,
    _PRC_TABLE_NAME,
    _QR_TABLE_NAME,
    _QU_TABLE_NAME,
    _RC_TABLE_NAME,
    _RS_TABLE_NAME,
    _RUNS_TABLE_NAME,
    _SQS_TABLE_NAME,
)

_SEARCH_RUN_COLUMNS = [
    "run_key",
    "query",
    "normalized_query",
    "research_goal",
    "num_results_requested",
    "rewrite_enabled",
    "session_id",
    "tool_name",
    "duration_ms",
    "final_result_count",
    "candidate_count",
    "has_more",
    "result_offset",
    "status",
    "error_type",
    "reranker_provider",
    "reranker_model",
    "payload_json",
]

_QUERY_UNDERSTANDING_COLUMNS = [
    "run_key",
    "intent",
    "confidence",
    "should_decompose",
    "rationale",
    "model",
    "model_used",
    "provider",
    "duration_ms",
    "fallback_used",
    "entities_count",
    "input_tokens",
    "output_tokens",
    "preserved_terms",
    "time_sensitivity",
    "payload_json",
]

_QUERY_REWRITES_COLUMNS = [
    "run_key",
    "variant_index",
    "branch_type",
    "kind",
    "target",
    "query",
    "weight",
    "reason",
    "max_results",
    "model",
    "model_used",
    "duration_ms",
    "input_tokens",
    "output_tokens",
    "payload_json",
]

_PROVIDER_CALL_COLUMNS = [
    "run_key",
    "provider",
    "branch_index",
    "branch_query",
    "num_results_requested",
    "num_results_returned",
    "duration_ms",
    "error_code",
    "error_message",
    "http_status",
    "tokens_used",
    "cost_usd",
    "payload_json",
]

_PROVIDER_CANDIDATE_COLUMNS = [
    "run_key",
    "provider",
    "branch_index",
    "rank",
    "title",
    "link",
    "snippet",
    "domain",
    "score",
    "published_date",
    "payload_json",
]

_MERGED_CANDIDATE_COLUMNS = [
    "run_key",
    "rank",
    "title",
    "link",
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
    "model_used",
    "input_count",
    "output_count",
    "input_tokens",
    "output_tokens",
    "duration_ms",
    "max_score",
    "avg_score",
    "score_threshold",
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
    "rank_before",
    "rank_after",
    "score_before",
    "score_after",
    "score_after_relevance",
    "score_after_recency",
    "score_after_entity",
    "recency_boost",
    "entity_overlap_score",
    "diversity_removed",
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
    "rewrite_variant_count",
    "provider_count",
    "branch_count",
    "total_candidates_input",
    "total_candidates_merged",
    "total_candidates_reranked",
    "total_final_results",
    "payload_json",
]

_JUDGE_EVALUATION_COLUMNS = [
    "run_key",
    "tool_name",
    "judge_model",
    "model_used",
    "relevance_score",
    "relevance_raw",
    "relevance_scale",
    "accuracy_score",
    "completeness_score",
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


_SEARCH_RUN_WRITER = TableWriter(
    table_name=_RUNS_TABLE_NAME,
    ensure_name="_ensure_search_runs",
    columns=_SEARCH_RUN_COLUMNS,
    defaults={"tool_name": "web_search"},
    task_name="analytics.search_run",
)
_QUERY_UNDERSTANDING_WRITER = TableWriter(
    table_name=_QU_TABLE_NAME,
    ensure_name="_ensure_query_understanding",
    columns=_QUERY_UNDERSTANDING_COLUMNS,
    task_name="analytics.query_understanding",
)
_QUERY_REWRITES_WRITER = TableWriter(
    table_name=_QR_TABLE_NAME,
    ensure_name="_ensure_query_rewrites",
    columns=_QUERY_REWRITES_COLUMNS,
    task_name="analytics.query_rewrites",
)
_PROVIDER_CALLS_WRITER = TableWriter(
    table_name=_PC_TABLE_NAME,
    ensure_name="_ensure_provider_calls",
    columns=_PROVIDER_CALL_COLUMNS,
    task_name="analytics.provider_calls",
)
_PROVIDER_CANDIDATES_WRITER = TableWriter(
    table_name=_PRC_TABLE_NAME,
    ensure_name="_ensure_provider_candidates",
    columns=_PROVIDER_CANDIDATE_COLUMNS,
    task_name="analytics.provider_candidates",
)
_MERGED_CANDIDATES_WRITER = TableWriter(
    table_name=_MC_TABLE_NAME,
    ensure_name="_ensure_merged_candidates",
    columns=_MERGED_CANDIDATE_COLUMNS,
    task_name="analytics.merged_candidates",
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
