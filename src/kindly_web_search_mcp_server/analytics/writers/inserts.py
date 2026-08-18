"""TableWriter instances for every analytics table."""

from __future__ import annotations

from .core import TableWriter
from .table_names import (
    _ABE_TABLE_NAME,
    _ABS_TABLE_NAME,
    _CE_TABLE_NAME,
    _CF_TABLE_NAME,
    _CO_TABLE_NAME,
    _CSD_TABLE_NAME,
    _CSE_TABLE_NAME,
    _CSH_TABLE_NAME,
    _CSHV_TABLE_NAME,
    _CSP_TABLE_NAME,
    _CSQV_TABLE_NAME,
    _CSREPO_TABLE_NAME,
    _CSRERANK_TABLE_NAME,
    _CSR_TABLE_NAME,
    _CSUMA_TABLE_NAME,
    _CSUM_TABLE_NAME,
    _FR_TABLE_NAME,
    _GSA_TABLE_NAME,
    _GSR_TABLE_NAME,
    _GSS_TABLE_NAME,
    _JE_TABLE_NAME,
    _LLM_CALL_LOG_TABLE_NAME,
    _PC_TABLE_NAME,
    _PR_TABLE_NAME,
    _QE_TABLE_NAME,
    _QUE_TABLE_NAME,
    _QV_TABLE_NAME,
    _QWSC_TABLE_NAME,
    _QWSR_TABLE_NAME,
    _RC_CAT_TABLE_NAME,
    _RC_TABLE_NAME,
    _RS_TABLE_NAME,
    _RUNS_TABLE_NAME,
    _SB_TABLE_NAME,
    _SC_TABLE_NAME,
    _SQS_TABLE_NAME,
    _TC_TABLE_NAME,
    _TOI_TABLE_NAME,
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
    "rewrite_prompt",
    "rewrite_model",
    "rewrite_input_tokens",
    "rewrite_output_tokens",
    "rewrite_latency_ms",
    "rewrite_error",
    "rewritten_branch_queries",
    "payload_json",
]

_SEARCH_BRANCH_COLUMNS = [
    "run_key",
    "branch_index",
    "branch_id",
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
    "request_query",
    "request_url",
    "http_status",
    "result_class",
    "response_meta_json",
    "retry_after_seconds",
    "retryable",
    "provider_call_id",
    "payload_json",
]

_TOOL_CALL_COLUMNS = [
    "event_id",
    "tool_call_id",
    "run_key",
    "session_id",
    "trace_id",
    "span_id",
    "tool_name",
    "phase",
    "status",
    "query",
    "research_goal",
    "input_url",
    "normalized_url",
    "input_count",
    "output_count",
    "duration_ms",
    "provider",
    "model",
    "input_tokens",
    "output_tokens",
    "request_fingerprint",
    "error_type",
    "error_message",
    "payload_json",
]

_QUERY_UNDERSTANDING_COLUMNS = [
    "run_key",
    "tool_call_id",
    "session_id",
    "raw_query",
    "normalized_query",
    "research_goal",
    "predicted_intent",
    "predicted_confidence",
    "final_intent",
    "final_confidence",
    "decision_path",
    "fallback_reason",
    "classifier_model",
    "classifier_provider",
    "classifier_endpoint",
    "classifier_latency_ms",
    "confidence_threshold",
    "scores_json",
    "entities_json",
    "preserved_terms",
    "compared_entities",
    "time_sensitivity",
    "domain_hints",
    "should_decompose",
    "rationale",
    "payload_json",
]

_SEARCH_CANDIDATE_COLUMNS = [
    "run_key",
    "link",
    "canonical_result_id",
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
    "relevance_raw",
    "relevance_scale",
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
    "status",
    "error_type",
    "error_message",
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

_LLM_CALL_LOG_COLUMNS = [
    "run_key",
    "call_purpose",
    "provider",
    "model",
    "input_tokens",
    "output_tokens",
    "tokens_used",
    "cost_usd",
    "duration_ms",
    "status",
    "error_type",
    "payload_json",
]

# ---------------------------------------------------------------------------
# Quick Web Search column lists
# ---------------------------------------------------------------------------
_QUICK_WEB_SEARCH_RUN_COLUMNS = [
    "terminal_event_id",
    "tool_call_id",
    "trace_id",
    "session_id",
    "search_id",
    "provider_session_id",
    "search_queries",
    "objective",
    "max_results",
    "max_chars_total",
    "max_chars_per_result",
    "client_model",
    "include_domains",
    "exclude_domains",
    "after_date",
    "location",
    "max_age_seconds",
    "timeout_seconds",
    "disable_cache_fallback",
    "status",
    "duration_ms",
    "total_citations",
    "warnings",
    "usage",
    "error_type",
    "error_message",
    "payload_json",
]

_QUICK_WEB_SEARCH_CITATION_COLUMNS = [
    "terminal_event_id",
    "tool_call_id",
    "citation_index",
    "title",
    "url",
    "snippet",
    "publish_date",
    "excerpts",
    "payload_json",
]

# ---------------------------------------------------------------------------
# Gemini Search column lists
# ---------------------------------------------------------------------------
_GEMINI_SEARCH_RUN_COLUMNS = [
    "terminal_event_id",
    "tool_call_id",
    "trace_id",
    "session_id",
    "query",
    "research_goal",
    "structured_output_requested",
    "mode",
    "answer",
    "structured_data",
    "search_queries",
    "model_used",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "grounding_chunks_count",
    "web_search_queries_count",
    "fallback_chain",
    "fallback_reason",
    "status",
    "duration_ms",
    "error_message",
    "payload_json",
]

_GEMINI_SEARCH_SOURCE_COLUMNS = [
    "terminal_event_id",
    "tool_call_id",
    "source_kind",
    "source_index",
    "url",
    "title",
    "source_json",
]

_GEMINI_SEARCH_ATTEMPT_COLUMNS = [
    "tool_call_id",
    "attempt_index",
    "branch_name",
    "model_requested",
    "model_used",
    "fallback_tier",
    "fallback_reason",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "grounding_chunk_count",
    "web_search_query_count",
    "status",
    "duration_ms",
    "error_type",
    "error_message",
    "payload_json",
]

# ---------------------------------------------------------------------------
# Code Search column lists
# ---------------------------------------------------------------------------
_CODE_SEARCH_RUN_COLUMNS = [
    "terminal_event_id",
    "tool_call_id",
    "trace_id",
    "session_id",
    "query",
    "research_goal",
    "language",
    "path",
    "filename",
    "extension",
    "regexp_requested",
    "deep_requested",
    "max_results_requested",
    "repo_name",
    "library_name",
    "topic",
    "repository_filters",
    "planner_original_query",
    "planner_search_text",
    "planner_api_query",
    "planner_mode",
    "planner_structural_kind",
    "planner_exa_semantic_query",
    "planner_regex_source",
    "planner_anchor_terms",
    "planner_concept_terms",
    "planner_source_tokens",
    "planner_qualifiers",
    "planner_warnings",
    "planner_backend_channels",
    "planner_variants",
    "planner_variant_kinds",
    "provider_response_count",
    "provider_hit_counts",
    "request_count",
    "hydration_count",
    "rerank_count",
    "returned_count",
    "repository_count",
    "diagnostic_count",
    "truncated",
    "dropped_count",
    "estimated_output_tokens",
    "duration_ms",
    "outcome",
    "error_type",
    "error_message",
    "payload_json",
]

_CODE_SEARCH_PROVIDER_COLUMNS = [
    "terminal_event_id",
    "response_index",
    "provider",
    "hit_count",
    "request_count",
    "outcome",
    "compiled_queries",
    "duration_ms",
    "error_type",
    "error_message",
    "payload_json",
]

_CODE_SEARCH_DIAGNOSTIC_COLUMNS = [
    "terminal_event_id",
    "diagnostic_index",
    "provider",
    "outcome",
    "failure_kind",
    "message",
    "status_code",
    "retry_after_seconds",
    "query",
    "details",
]

_CODE_SEARCH_HIT_COLUMNS = [
    "terminal_event_id",
    "hit_rank",
    "url",
    "repository",
    "path",
    "sha",
    "provider",
    "query_variant",
    "search_rank",
    "result_kind",
    "evidence_role",
    "title",
    "snippet",
    "published_date",
    "final_score",
    "score_components",
    "reasons",
    "hydrated",
    "hydrated_source_truncated",
    "line_start",
    "line_end",
    "commit_oid",
    "fragment_count",
    "symbol_count",
    "match_span_count",
    "location_precision",
    "lines_available",
    "revision_available",
    "match_data_available",
    "source_metadata",
    "payload_json",
]

_CODE_SEARCH_HIT_VARIANT_COLUMNS = [
    "terminal_event_id",
    "hit_rank",
    "association_index",
    "variant_index",
    "provider",
    "query_variant",
    "search_rank",
]

_CODE_SEARCH_QUERY_VARIANT_COLUMNS = [
    "terminal_event_id",
    "variant_index",
    "query_text",
    "variant_kind",
]

_CODE_SEARCH_REPOSITORY_COLUMNS = [
    "terminal_event_id",
    "repository_index",
    "name_with_owner",
    "url",
    "description",
    "stars",
    "forks",
    "pushed_at",
    "language",
    "topics",
    "license_spdx_id",
    "homepage_url",
    "default_branch",
    "head_oid",
    "archived",
    "fork",
    "discovery_rank",
    "discovery_score",
    "discovery_queries",
    "proof_hits",
    "proof_paths",
    "proof_providers",
    "verified",
    "payload_json",
]

_CODE_SEARCH_RERANK_COLUMNS = [
    "terminal_event_id",
    "provider",
    "model",
    "input_count",
    "output_count",
    "reranked_count",
    "status",
    "diagnostic_outcome",
    "diagnostic_message",
    "duration_ms",
    "payload_json",
]

# ---------------------------------------------------------------------------
# Content Operations and Summary column lists
# ---------------------------------------------------------------------------
_CONTENT_OPERATION_COLUMNS = [
    "terminal_event_id",
    "tool_call_id",
    "trace_id",
    "session_id",
    "tool_name",
    "input_count",
    "output_count",
    "duration_ms",
    "status",
    "error_type",
    "error_message",
    "payload_json",
]

_CONTENT_FETCH_COLUMNS = [
    "terminal_event_id",
    "tool_call_id",
    "item_index",
    "input_url",
    "normalized_url",
    "fetched_url",
    "source_type",
    "fetch_backend",
    "status",
    "content_length",
    "page_char_count",
    "word_count",
    "window_offset",
    "window_length",
    "window_returned_chars",
    "window_total_chars",
    "window_has_more",
    "window_next_offset",
    "item_duration_ms",
    "payload_json",
]

_CONTENT_SUMMARY_COLUMNS = [
    "terminal_event_id",
    "tool_call_id",
    "item_index",
    "normalized_url",
    "focus_query",
    "input_chars",
    "source_url_count",
    "is_batch",
    "batch_size",
    "is_stub",
    "backend",
    "model_requested",
    "model_used",
    "fallback_attempted",
    "fallback_tier",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "summary_length_chars",
    "key_points_count",
    "important_entities_count",
    "verbatim_terms_count",
    "limitations_count",
    "source_date",
    "status",
    "error_type",
    "error_message",
    "duration_ms",
    "payload_json",
]

_CONTENT_SUMMARY_ATTEMPT_COLUMNS = [
    "tool_call_id",
    "item_index",
    "attempt_index",
    "is_batch",
    "batch_size",
    "backend",
    "model_requested",
    "model_used",
    "fallback_tier",
    "source_url_count",
    "input_chars",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "duration_ms",
    "status",
    "error_type",
    "error_message",
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
    defaults={"status": "unknown"},
    task_name="analytics.provider_calls",
)
_TOOL_CALLS_WRITER = TableWriter(
    table_name=_TC_TABLE_NAME,
    ensure_name="_ensure_tool_calls",
    columns=_TOOL_CALL_COLUMNS,
    task_name="analytics.tool_calls",
)
_QUERY_UNDERSTANDING_WRITER = TableWriter(
    table_name=_QUE_TABLE_NAME,
    ensure_name="_ensure_query_understanding_events",
    columns=_QUERY_UNDERSTANDING_COLUMNS,
    task_name="analytics.query_understanding_events",
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
    defaults={"survived": True, "diversity_removed": False},
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
    defaults={"status": "success"},
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
_LLM_CALL_LOG_WRITER = TableWriter(
    table_name=_LLM_CALL_LOG_TABLE_NAME,
    ensure_name="_ensure_llm_call_log",
    columns=_LLM_CALL_LOG_COLUMNS,
    task_name="analytics.llm_call_log",
)

# ---------------------------------------------------------------------------
# New fact TableWriter instances
# ---------------------------------------------------------------------------
_QUICK_WEB_SEARCH_RUNS_WRITER = TableWriter(
    table_name=_QWSR_TABLE_NAME,
    ensure_name="_ensure_quick_web_search_runs",
    columns=_QUICK_WEB_SEARCH_RUN_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.quick_web_search_runs",
)
_QUICK_WEB_SEARCH_CITATIONS_WRITER = TableWriter(
    table_name=_QWSC_TABLE_NAME,
    ensure_name="_ensure_quick_web_search_citations",
    columns=_QUICK_WEB_SEARCH_CITATION_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.quick_web_search_citations",
)
_GEMINI_SEARCH_RUNS_WRITER = TableWriter(
    table_name=_GSR_TABLE_NAME,
    ensure_name="_ensure_gemini_search_runs",
    columns=_GEMINI_SEARCH_RUN_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.gemini_search_runs",
)
_GEMINI_SEARCH_SOURCES_WRITER = TableWriter(
    table_name=_GSS_TABLE_NAME,
    ensure_name="_ensure_gemini_search_sources",
    columns=_GEMINI_SEARCH_SOURCE_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.gemini_search_sources",
)
_GEMINI_SEARCH_ATTEMPTS_WRITER = TableWriter(
    table_name=_GSA_TABLE_NAME,
    ensure_name="_ensure_gemini_search_attempts",
    columns=_GEMINI_SEARCH_ATTEMPT_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.gemini_search_attempts",
)
_CODE_SEARCH_RUNS_WRITER = TableWriter(
    table_name=_CSR_TABLE_NAME,
    ensure_name="_ensure_code_search_runs",
    columns=_CODE_SEARCH_RUN_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.code_search_runs",
)
_CODE_SEARCH_PROVIDERS_WRITER = TableWriter(
    table_name=_CSP_TABLE_NAME,
    ensure_name="_ensure_code_search_providers",
    columns=_CODE_SEARCH_PROVIDER_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.code_search_providers",
)
_CODE_SEARCH_DIAGNOSTICS_WRITER = TableWriter(
    table_name=_CSD_TABLE_NAME,
    ensure_name="_ensure_code_search_diagnostics",
    columns=_CODE_SEARCH_DIAGNOSTIC_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.code_search_diagnostics",
)
_CODE_SEARCH_HITS_WRITER = TableWriter(
    table_name=_CSH_TABLE_NAME,
    ensure_name="_ensure_code_search_hits",
    columns=_CODE_SEARCH_HIT_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.code_search_hits",
)
_CODE_SEARCH_HIT_VARIANTS_WRITER = TableWriter(
    table_name=_CSHV_TABLE_NAME,
    ensure_name="_ensure_code_search_hit_variants",
    columns=_CODE_SEARCH_HIT_VARIANT_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.code_search_hit_variants",
)
_CODE_SEARCH_QUERY_VARIANTS_WRITER = TableWriter(
    table_name=_CSQV_TABLE_NAME,
    ensure_name="_ensure_code_search_query_variants",
    columns=_CODE_SEARCH_QUERY_VARIANT_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.code_search_query_variants",
)
_CODE_SEARCH_REPOSITORIES_WRITER = TableWriter(
    table_name=_CSREPO_TABLE_NAME,
    ensure_name="_ensure_code_search_repositories",
    columns=_CODE_SEARCH_REPOSITORY_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.code_search_repositories",
)
_CODE_SEARCH_RERANK_WRITER = TableWriter(
    table_name=_CSRERANK_TABLE_NAME,
    ensure_name="_ensure_code_search_rerank",
    columns=_CODE_SEARCH_RERANK_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.code_search_rerank",
)
_CONTENT_OPERATIONS_WRITER = TableWriter(
    table_name=_CO_TABLE_NAME,
    ensure_name="_ensure_content_operations",
    columns=_CONTENT_OPERATION_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.content_operations",
)
_CONTENT_FETCHES_WRITER = TableWriter(
    table_name=_CF_TABLE_NAME,
    ensure_name="_ensure_content_fetches",
    columns=_CONTENT_FETCH_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.content_fetches",
)
_CONTENT_SUMMARIES_WRITER = TableWriter(
    table_name=_CSUM_TABLE_NAME,
    ensure_name="_ensure_content_summaries",
    columns=_CONTENT_SUMMARY_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.content_summaries",
)
_CONTENT_SUMMARY_ATTEMPTS_WRITER = TableWriter(
    table_name=_CSUMA_TABLE_NAME,
    ensure_name="_ensure_content_summary_attempts",
    columns=_CONTENT_SUMMARY_ATTEMPT_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.content_summary_attempts",
)

# ---------------------------------------------------------------------------
# Web search funnel uplift column lists
# ---------------------------------------------------------------------------
_RESULT_CATALOG_COLUMNS = [
    "canonical_result_id",
    "canonical_url",
    "domain",
    "title_first_seen",
    "first_seen_at",
    "first_seen_run_key",
    "total_run_appearances",
]

_PROVIDER_RESULT_COLUMNS = [
    "provider_result_id",
    "provider_call_id",
    "run_key",
    "branch_id",
    "provider",
    "provider_rank",
    "canonical_result_id",
    "raw_url",
    "title",
    "snippet",
    "raw_score",
    "is_eligible",
    "rejection_reason",
    "recorded_at",
    "payload_json",
]

_QUERY_VARIANT_COLUMNS = [
    "variant_id",
    "run_key",
    "variant_order",
    "variant_role",
    "query_text",
    "selected",
    "executed",
    "skip_reason",
    "recorded_at",
]

_CANDIDATE_STAGE_EVENT_COLUMNS = [
    "stage_execution_id",
    "run_key",
    "canonical_result_id",
    "entered",
    "survived",
    "rank_before",
    "rank_after",
    "score_before",
    "score_after",
    "score_name",
    "removal_reason",
    "recorded_at",
    "payload_json",
]

_TOOL_OUTPUT_ITEM_COLUMNS = [
    "output_item_id",
    "tool_call_id",
    "session_id",
    "run_key",
    "tool_name",
    "item_type",
    "item_rank",
    "canonical_result_id",
    "raw_url",
    "title",
    "snippet",
    "recorded_at",
]

# ---------------------------------------------------------------------------
# Web search funnel uplift TableWriter instances
# ---------------------------------------------------------------------------
_RESULT_CATALOG_WRITER = TableWriter(
    table_name=_RC_CAT_TABLE_NAME,
    ensure_name="_ensure_result_catalog",
    columns=_RESULT_CATALOG_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.result_catalog",
)
_PROVIDER_RESULTS_WRITER = TableWriter(
    table_name=_PR_TABLE_NAME,
    ensure_name="_ensure_provider_results",
    columns=_PROVIDER_RESULT_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.provider_results",
)
_QUERY_VARIANTS_WRITER = TableWriter(
    table_name=_QV_TABLE_NAME,
    ensure_name="_ensure_query_variants",
    columns=_QUERY_VARIANT_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.query_variants",
)
_CANDIDATE_STAGE_EVENTS_WRITER = TableWriter(
    table_name=_CSE_TABLE_NAME,
    ensure_name="_ensure_candidate_stage_events",
    columns=_CANDIDATE_STAGE_EVENT_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.candidate_stage_events",
)
_TOOL_OUTPUT_ITEMS_WRITER = TableWriter(
    table_name=_TOI_TABLE_NAME,
    ensure_name="_ensure_tool_output_items",
    columns=_TOOL_OUTPUT_ITEM_COLUMNS,
    on_conflict="ON CONFLICT DO NOTHING",
    task_name="analytics.tool_output_items",
)
