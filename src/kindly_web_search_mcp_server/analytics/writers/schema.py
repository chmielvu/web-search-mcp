"""CREATE TABLE schema bootstrap for analytics writers.

Clean-cutover redesign: 7 wide fact tables at clear pipeline grains +
2 embedding tables for vss vector similarity search.  Old ``search_events``
log and 5 of 6 observability tables are dropped entirely.
``provider_health_transitions`` moved here from ``observability_schema.py``.
"""

from __future__ import annotations

import logging

import duckdb

from .connection import (
    _db_path,
    _ensure_columns,
    _ensure_flockmtl_resources_table,
    _install_flockmtl_once,
    _LOCK,
    ensure_flockmtl_loaded,
    ensure_flockmtl_resources,
)
from .table_names import (
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
    _PH_TABLE_NAME,
    _PR_TABLE_NAME,
    _QE_TABLE_NAME,
    _QUE_TABLE_NAME,
    _QV_TABLE_NAME,
    _QWSC_TABLE_NAME,
    _QWSR_TABLE_NAME,
    _RC_CAT_TABLE_NAME,
    _RC_TABLE_NAME,
    _RL_TABLE_NAME,
    _RS_TABLE_NAME,
    _RUNS_TABLE_NAME,
    _SB_TABLE_NAME,
    _SC_TABLE_NAME,
    _SQS_TABLE_NAME,
    _TC_TABLE_NAME,
    _TOI_TABLE_NAME,
)

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _create_table(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
    ddl_body: str,
) -> None:
    """Execute a raw CREATE TABLE IF NOT EXISTS statement."""
    connection.execute(f"CREATE TABLE IF NOT EXISTS {table_name} (\n{ddl_body}\n)")


# ---------------------------------------------------------------------------
# 1. search_runs — one row per search run (Grain 1: per-request)
# ---------------------------------------------------------------------------
def _ensure_search_runs(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _RUNS_TABLE_NAME,
        """
        recorded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
        run_key               VARCHAR NOT NULL,
        tool_call_id          VARCHAR,
        session_id            VARCHAR,
        query                 VARCHAR NOT NULL,
        normalized_query      VARCHAR,
        research_goal         VARCHAR,
        intent                VARCHAR,
        understanding_confidence DOUBLE,
        num_results_requested INTEGER,
        rewrite_enabled       BOOLEAN,
        selected_providers    VARCHAR[],
        skipped_providers     VARCHAR[],
        branch_count          INTEGER,
        provider_count        INTEGER,
        merged_count          INTEGER,
        reranked_count        INTEGER,
        final_result_count    INTEGER,
        candidate_count       INTEGER,
        status                VARCHAR,
        error_type            VARCHAR,
        duration_ms           DOUBLE,
        reranker_provider     VARCHAR,
        reranker_model        VARCHAR,
        rake_terms             VARCHAR[],
        brave_autosuggest     VARCHAR[],
        rewrite_prompt        VARCHAR,
        rewrite_model         VARCHAR,
        rewrite_input_tokens  INTEGER,
        rewrite_output_tokens INTEGER,
        rewrite_latency_ms    DOUBLE,
        rewrite_error         VARCHAR,
        rewritten_branch_queries VARCHAR[],   -- 5 rewrites from the planner (k1, k2, k3, neural, specialized)
        payload_json          JSON
        """,
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_runs_run_key ON search_runs(run_key)")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_runs_recorded_at ON search_runs(recorded_at)"
    )


# ---------------------------------------------------------------------------
# 2. search_branches — one row per branch per run
# ---------------------------------------------------------------------------
def _ensure_search_branches(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _SB_TABLE_NAME,
        """
        recorded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        run_key            VARCHAR NOT NULL,
        branch_index       INTEGER NOT NULL,
        branch_role        VARCHAR NOT NULL,
        branch_query       VARCHAR NOT NULL,
        branch_why         VARCHAR,
        support_terms      VARCHAR[],
        max_results        INTEGER,
        assigned_providers VARCHAR[],
        attempted_providers VARCHAR[],
        skipped_providers  VARCHAR[],
        results_count      INTEGER,
        latency_ms         DOUBLE,
        payload_json       JSON
        """,
    )


# ---------------------------------------------------------------------------
# 3. provider_calls — one row per provider call per branch (Grain 2)
# ---------------------------------------------------------------------------
def _ensure_provider_calls(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _PC_TABLE_NAME,
        """
        recorded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
        run_key               VARCHAR NOT NULL,
        branch_index          INTEGER,
        branch_role           VARCHAR,
        provider              VARCHAR NOT NULL,
        branch_query          VARCHAR,
        status                VARCHAR NOT NULL,
        num_results_requested INTEGER,
        num_results_returned  INTEGER,
        latency_ms            DOUBLE,
        error_type            VARCHAR,
        error_message         VARCHAR,
        candidate_urls        VARCHAR[],
        request_query         VARCHAR,
        request_url           VARCHAR,
        http_status           INTEGER,
        result_class          VARCHAR,
        response_meta_json    JSON,
        retry_after_seconds   DOUBLE,
        retryable             BOOLEAN,
        provider_call_id      VARCHAR,
        payload_json          JSON
        """,
    )


def _ensure_tool_calls(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _TC_TABLE_NAME,
        """
        recorded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        event_id             VARCHAR NOT NULL,
        tool_call_id         VARCHAR,
        session_id           VARCHAR,
        trace_id             VARCHAR,
        span_id              VARCHAR,
        tool_name            VARCHAR NOT NULL,
        phase                VARCHAR NOT NULL,
        status               VARCHAR,
        query                VARCHAR,
        research_goal        VARCHAR,
        input_url            VARCHAR,
        normalized_url       VARCHAR,
        input_count          INTEGER,
        output_count         INTEGER,
        duration_ms          DOUBLE,
        provider             VARCHAR,
        model                VARCHAR,
        input_tokens         INTEGER,
        output_tokens        INTEGER,
        request_fingerprint  VARCHAR,
        error_type           VARCHAR,
        error_message        VARCHAR,
        payload_json         JSON
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_tool_calls_tool_recorded "
        "ON tool_calls(tool_name, recorded_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_tool_calls_tool_call_id ON tool_calls(tool_call_id)"
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_tool_calls_status ON tool_calls(status)")


def _ensure_query_understanding_events(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _QUE_TABLE_NAME,
        """
        recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
        run_key                 VARCHAR,
        tool_call_id            VARCHAR,
        session_id              VARCHAR,
        raw_query               VARCHAR NOT NULL,
        normalized_query        VARCHAR,
        research_goal           VARCHAR,
        predicted_intent        VARCHAR,
        predicted_confidence    DOUBLE,
        final_intent            VARCHAR NOT NULL,
        final_confidence        DOUBLE,
        decision_path            VARCHAR NOT NULL,
        fallback_reason         VARCHAR,
        classifier_model        VARCHAR,
        classifier_provider     VARCHAR,
        classifier_endpoint     VARCHAR,
        classifier_latency_ms   DOUBLE,
        confidence_threshold    DOUBLE,
        scores_json             JSON,
        entities_json           JSON,
        preserved_terms         VARCHAR[],
        compared_entities       VARCHAR[],
        time_sensitivity        VARCHAR,
        domain_hints            VARCHAR[],
        should_decompose        BOOLEAN,
        rationale               VARCHAR,
        payload_json            JSON
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_query_understanding_intent_recorded "
        "ON query_understanding_events(predicted_intent, recorded_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_query_understanding_run_key "
        "ON query_understanding_events(run_key)"
    )


# ---------------------------------------------------------------------------
# 4. search_candidates — one row per unique candidate URL per run
# ---------------------------------------------------------------------------
def _ensure_search_candidates(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _SC_TABLE_NAME,
        """
        recorded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        run_key        VARCHAR NOT NULL,
        link           VARCHAR NOT NULL,
        canonical_result_id VARCHAR,
        title          VARCHAR,
        snippet        VARCHAR,
        domain         VARCHAR,
        rrf_score      DOUBLE,
        provider_count INTEGER,
        providers      VARCHAR[],
        overlap_flag   BOOLEAN,
        payload_json   JSON
        """,
    )


# ---------------------------------------------------------------------------
# 5. rerank_stages — one row per rerank stage per run (Grain 4)
# ---------------------------------------------------------------------------
def _ensure_rerank_stages(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _RS_TABLE_NAME,
        """
        recorded_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
        run_key                  VARCHAR NOT NULL,
        stage                    VARCHAR NOT NULL,
        provider                 VARCHAR,
        model                    VARCHAR,
        input_count              INTEGER,
        output_count             INTEGER,
        duration_ms              DOUBLE,
        max_score                DOUBLE,
        avg_score                DOUBLE,
        score_threshold          DOUBLE,
        alpha_blend              DOUBLE,
        input_tokens             INTEGER,
        output_tokens            INTEGER,
        status                   VARCHAR,
        error_type               VARCHAR,
        instruction_present      BOOLEAN,
        instruction_length       INTEGER,
        query_type_hint          VARCHAR,
        entity_overlap_enabled   BOOLEAN,
        payload_json             JSON
        """,
    )


# ---------------------------------------------------------------------------
# 6. rerank_candidates — one row per candidate per stage (Grain 3)
# ---------------------------------------------------------------------------
def _ensure_rerank_candidates(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _RC_TABLE_NAME,
        """
        recorded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        run_key              VARCHAR NOT NULL,
        stage                VARCHAR NOT NULL,
        link                 VARCHAR NOT NULL,
        candidate_id         VARCHAR,
        canonical_result_id  VARCHAR,
        rank_before          INTEGER,
        rank_after           INTEGER,
        score_before         DOUBLE,
        score_after          DOUBLE,
        bm25_score           DOUBLE,
        bm25_rank            INTEGER,
        dense_score          DOUBLE,
        dense_rank           INTEGER,
        cross_encoder_raw    DOUBLE,
        llm_raw_score        DOUBLE,
        fused_score          DOUBLE,
        hybrid_rrf_score     DOUBLE,
        recency_boost        DOUBLE,
        entity_overlap_score DOUBLE,
        survived             BOOLEAN NOT NULL,
        diversity_removed    BOOLEAN NOT NULL DEFAULT FALSE,
        payload_json         JSON
        """,
    )
    _ensure_columns(
        connection,
        _RC_TABLE_NAME,
        {"diversity_removed": "BOOLEAN"},
    )


# ---------------------------------------------------------------------------
# 7. final_results — one row per returned result
# ---------------------------------------------------------------------------
def _ensure_final_results(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _FR_TABLE_NAME,
        """
        recorded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        run_key              VARCHAR NOT NULL,
        rank                 INTEGER,
        title                VARCHAR,
        link                 VARCHAR,
        snippet              VARCHAR,
        domain               VARCHAR,
        final_score          DOUBLE,
        providers            VARCHAR[],
        provider_count       INTEGER,
        entities_count       INTEGER,
        candidate_id         VARCHAR,
        canonical_result_id  VARCHAR,
        payload_json         JSON
        """,
    )


# ---------------------------------------------------------------------------
# 8. query_embeddings — one row per run, stores the query embedding
# ---------------------------------------------------------------------------
def _ensure_query_embeddings(connection: duckdb.DuckDBPyConnection) -> None:
    ensure_vss_loaded(connection)
    _create_table(
        connection,
        _QE_TABLE_NAME,
        """
        recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        run_key       VARCHAR NOT NULL,
        embedding     FLOAT[1024],
        model_id      VARCHAR DEFAULT 'intfloat/multilingual-e5-large-instruct',
        payload_json  JSON
        """,
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_qemb_run_key ON query_embeddings(run_key)")


# ---------------------------------------------------------------------------
# 9. candidate_embeddings — one row per candidate per run
# ---------------------------------------------------------------------------
def _ensure_candidate_embeddings(connection: duckdb.DuckDBPyConnection) -> None:
    ensure_vss_loaded(connection)
    _create_table(
        connection,
        _CE_TABLE_NAME,
        """
        recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        run_key       VARCHAR NOT NULL,
        link          VARCHAR NOT NULL,
        title         VARCHAR,
        embedding     FLOAT[1024],
        model_id      VARCHAR DEFAULT 'intfloat/multilingual-e5-large-instruct',
        payload_json  JSON
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_cemb_run_key ON candidate_embeddings(run_key)"
    )


# ---------------------------------------------------------------------------
# provider_health_transitions — moved from observability_schema.py
# ---------------------------------------------------------------------------
def _ensure_provider_health_transitions(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    _create_table(
        connection,
        _PH_TABLE_NAME,
        """
        provider VARCHAR NOT NULL,
        transition VARCHAR NOT NULL,
        run_key VARCHAR,
        tool_call_id VARCHAR,
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        status VARCHAR,
        consecutive_failures INTEGER,
        cooldown_seconds DOUBLE,
        cooldown_remaining_s DOUBLE,
        total_successes INTEGER,
        total_failures INTEGER,
        error_type VARCHAR,
        is_rate_limit BOOLEAN,
        circuit_state VARCHAR,
        payload_json JSON
        """,
    )


# ---------------------------------------------------------------------------
# 10. llm_call_log — unified cost tracking across all LLM calls
# ---------------------------------------------------------------------------
def _ensure_llm_call_log(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _LLM_CALL_LOG_TABLE_NAME,
        """
        recorded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
        run_key               VARCHAR NOT NULL,
        call_purpose          VARCHAR NOT NULL,
        provider              VARCHAR,
        model                 VARCHAR,
        input_tokens          INTEGER,
        output_tokens         INTEGER,
        tokens_used           INTEGER,
        cost_usd              DOUBLE,
        duration_ms           DOUBLE,
        status                VARCHAR,
        error_type            VARCHAR,
        payload_json          JSON
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_call_log_run_key ON llm_call_log(run_key)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_call_log_purpose ON llm_call_log(call_purpose)"
    )


# ---------------------------------------------------------------------------
# 11. llm_judgments — persisted FlockMTL verdicts on search runs
# Populated by `analytics/judges.py::judge_search_run` after each search
# completes. Read by `vw_llm_judgments`. No per-row llm_complete — every
# row here is one already-billed LLM call.
# ---------------------------------------------------------------------------
def _ensure_llm_judgments(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        "llm_judgments",
        """
        recorded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        run_key            VARCHAR NOT NULL,
        judgment_kind      VARCHAR NOT NULL,  -- 'run_overview' | 'intent_coherence' | 'rewrite_coverage' | 'rerank_improvement' | 'result_quality' | 'failure_cause'
        judgment_target    VARCHAR,           -- link, run_key, or 'run_key:rank' — what was judged
        prompt_name        VARCHAR NOT NULL,
        model_name         VARCHAR NOT NULL,
        verdict            VARCHAR NOT NULL,  -- raw LLM output (category, grade, YES/NO)
        input_tokens       INTEGER,
        output_tokens      INTEGER,
        duration_ms        DOUBLE,
        status             VARCHAR NOT NULL,  -- 'success' | 'error'
        error_message      VARCHAR,
        payload_json       JSON,
        facet              VARCHAR,
        reasoning          VARCHAR,
        rubric_version     VARCHAR NOT NULL DEFAULT 'v1',
        confidence         SMALLINT,
        context_shown      JSON
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_judgments_run_key ON llm_judgments(run_key)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_judgments_kind ON llm_judgments(judgment_kind)"
    )


# ---------------------------------------------------------------------------
# Judge rubric catalog + calibration set (populated by judge_calibration.py)
# ---------------------------------------------------------------------------
def _ensure_judge_rubrics(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        "judge_rubrics",
        """
        rubric_version VARCHAR NOT NULL,
        facet          VARCHAR NOT NULL,
        model_name     VARCHAR NOT NULL,
        prompt_name    VARCHAR NOT NULL,
        fewshot_json   JSON,
        is_active      BOOLEAN NOT NULL DEFAULT true,
        kappa_score    DOUBLE,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (rubric_version, facet, model_name)
        """,
    )


def _ensure_judge_calibration_set(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        "judge_calibration_set",
        """
        run_key        VARCHAR NOT NULL,
        facet          VARCHAR NOT NULL,
        model_name     VARCHAR NOT NULL,
        human_verdict  VARCHAR,
        judge_verdict  VARCHAR,
        adjudicator    VARCHAR,
        adjudicated_at TIMESTAMPTZ,
        rubric_version VARCHAR NOT NULL,
        PRIMARY KEY (run_key, facet, model_name)
        """,
    )


# ---------------------------------------------------------------------------
# Quality / judge tables (kept, with upgraded judge_evaluations DDL)
# ---------------------------------------------------------------------------
def _ensure_search_quality_scores(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    _create_table(
        connection,
        _SQS_TABLE_NAME,
        """
        run_key               VARCHAR NOT NULL PRIMARY KEY,
        recorded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
        provider_overlap_rate DOUBLE,
        domain_diversity_count INTEGER,
        domain_diversity_ratio DOUBLE,
        rerank_compression_ratio DOUBLE,
        avg_rrf_score         DOUBLE,
        top_score             DOUBLE,
        p95_score             DOUBLE,
        provider_count        INTEGER,
        branch_count          INTEGER,
        total_candidates_input INTEGER,
        total_candidates_merged INTEGER,
        total_candidates_reranked INTEGER,
        total_final_results   INTEGER,
        ndcg_at_10            DOUBLE,
        payload_json          JSON
        """,
    )


def _ensure_judge_evaluations(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    _create_table(
        connection,
        _JE_TABLE_NAME,
        """
        run_key              VARCHAR NOT NULL,
        recorded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        evaluated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        tool_name            VARCHAR,
        judge_model          VARCHAR,
        model_used           VARCHAR,
        link                 VARCHAR,
        relevance_grade      VARCHAR,
        relevance_score      DOUBLE,
        relevance_raw        INTEGER,
        relevance_scale      VARCHAR,
        accuracy_grade       VARCHAR,
        accuracy_score       DOUBLE,
        completeness_grade   VARCHAR,
        completeness_score   DOUBLE,
        source_quality_grade VARCHAR,
        source_quality_score DOUBLE,
        overall_score        DOUBLE,
        rationale            VARCHAR,
        duration_ms          DOUBLE,
        input_tokens         INTEGER,
        output_tokens        INTEGER,
        tokens_used          INTEGER,
        cost_usd             DOUBLE,
        status               VARCHAR NOT NULL DEFAULT 'success',
        error_type           VARCHAR,
        error_message        VARCHAR,
        payload_json         JSON
        """,
    )

# ---------------------------------------------------------------------------
# Quick Web Search tables
# ---------------------------------------------------------------------------
def _ensure_quick_web_search_runs(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _QWSR_TABLE_NAME,
        """
        terminal_event_id       VARCHAR NOT NULL PRIMARY KEY,
        tool_call_id            VARCHAR NOT NULL,
        recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
        trace_id                VARCHAR,
        session_id              VARCHAR,
        search_id               VARCHAR,
        provider_session_id     VARCHAR,
        search_queries          VARCHAR[],
        objective               VARCHAR,
        max_results             INTEGER,
        max_chars_total         INTEGER,
        max_chars_per_result    INTEGER,
        client_model            VARCHAR,
        include_domains         VARCHAR[],
        exclude_domains         VARCHAR[],
        after_date              VARCHAR,
        location                VARCHAR,
        max_age_seconds         INTEGER,
        timeout_seconds         DOUBLE,
        disable_cache_fallback  BOOLEAN,
        status                  VARCHAR,
        duration_ms             DOUBLE,
        total_citations         INTEGER,
        warnings                JSON,
        usage                   JSON,
        error_type              VARCHAR,
        error_message           VARCHAR,
        payload_json            JSON
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_qwsr_tool_call_id ON quick_web_search_runs(tool_call_id)"
    )


def _ensure_quick_web_search_citations(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _QWSC_TABLE_NAME,
        """
        terminal_event_id VARCHAR NOT NULL,
        tool_call_id      VARCHAR NOT NULL,
        citation_index    INTEGER NOT NULL,
        title             VARCHAR,
        url               VARCHAR,
        snippet           VARCHAR,
        publish_date      VARCHAR,
        excerpts          VARCHAR[],
        payload_json      JSON,
        PRIMARY KEY (terminal_event_id, citation_index)
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_qwsc_tool_call_id ON quick_web_search_citations(tool_call_id)"
    )


# ---------------------------------------------------------------------------
# Gemini Search tables
# ---------------------------------------------------------------------------
def _ensure_gemini_search_runs(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _GSR_TABLE_NAME,
        """
        terminal_event_id            VARCHAR NOT NULL PRIMARY KEY,
        tool_call_id                 VARCHAR NOT NULL,
        recorded_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
        trace_id                     VARCHAR,
        session_id                   VARCHAR,
        query                        VARCHAR NOT NULL,
        research_goal                VARCHAR,
        structured_output_requested  BOOLEAN,
        mode                         VARCHAR,
        answer                       VARCHAR,
        structured_data              JSON,
        search_queries               VARCHAR[],
        model_used                   VARCHAR,
        prompt_tokens                INTEGER,
        completion_tokens            INTEGER,
        total_tokens                 INTEGER,
        grounding_chunks_count       INTEGER,
        web_search_queries_count     INTEGER,
        fallback_chain               VARCHAR[],
        fallback_reason              VARCHAR,
        status                       VARCHAR,
        duration_ms                  DOUBLE,
        error_message                VARCHAR,
        payload_json                 JSON
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_gsr_tool_call_id ON gemini_search_runs(tool_call_id)"
    )


def _ensure_gemini_search_sources(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _GSS_TABLE_NAME,
        """
        terminal_event_id VARCHAR NOT NULL,
        tool_call_id      VARCHAR NOT NULL,
        source_kind       VARCHAR NOT NULL,
        source_index      INTEGER NOT NULL,
        url               VARCHAR,
        title             VARCHAR,
        source_json       JSON,
        PRIMARY KEY (terminal_event_id, source_kind, source_index)
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_gss_tool_call_id ON gemini_search_sources(tool_call_id)"
    )


def _ensure_gemini_search_attempts(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _GSA_TABLE_NAME,
        """
        tool_call_id            VARCHAR NOT NULL,
        attempt_index           INTEGER NOT NULL,
        branch_name             VARCHAR,
        model_requested         VARCHAR,
        model_used              VARCHAR,
        fallback_tier           INTEGER,
        fallback_reason         VARCHAR,
        prompt_tokens           INTEGER,
        completion_tokens       INTEGER,
        total_tokens            INTEGER,
        grounding_chunk_count   INTEGER,
        web_search_query_count  INTEGER,
        status                  VARCHAR,
        duration_ms             DOUBLE,
        error_type              VARCHAR,
        error_message           VARCHAR,
        payload_json            JSON,
        PRIMARY KEY (tool_call_id, attempt_index)
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_gsa_tool_call_id ON gemini_search_attempts(tool_call_id)"
    )


# ---------------------------------------------------------------------------
# Code Search tables
# ---------------------------------------------------------------------------
def _ensure_code_search_runs(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _CSR_TABLE_NAME,
        """
        terminal_event_id          VARCHAR NOT NULL PRIMARY KEY,
        tool_call_id               VARCHAR NOT NULL,
        recorded_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
        trace_id                   VARCHAR,
        session_id                 VARCHAR,
        query                      VARCHAR NOT NULL,
        research_goal              VARCHAR,
        language                   VARCHAR,
        path                       VARCHAR,
        filename                   VARCHAR,
        extension                  VARCHAR,
        regexp_requested           BOOLEAN,
        deep_requested             BOOLEAN,
        max_results_requested      INTEGER,
        repo_name                  VARCHAR,
        library_name               VARCHAR,
        topic                      VARCHAR,
        repository_filters         VARCHAR[],
        planner_original_query     VARCHAR,
        planner_search_text        VARCHAR,
        planner_api_query          VARCHAR,
        planner_mode               VARCHAR,
        planner_structural_kind    VARCHAR,
        planner_exa_semantic_query VARCHAR,
        planner_regex_source       VARCHAR,
        planner_anchor_terms       VARCHAR[],
        planner_concept_terms      VARCHAR[],
        planner_source_tokens      JSON,
        planner_qualifiers         JSON,
        planner_warnings           VARCHAR[],
        planner_backend_channels   VARCHAR[],
        planner_variants           VARCHAR[],
        planner_variant_kinds      VARCHAR[],
        provider_response_count    INTEGER,
        provider_hit_counts        JSON,
        request_count              INTEGER,
        hydration_count            INTEGER,
        rerank_count               INTEGER,
        returned_count             INTEGER,
        repository_count           INTEGER,
        diagnostic_count           INTEGER,
        truncated                  BOOLEAN,
        dropped_count              INTEGER,
        estimated_output_tokens    INTEGER,
        duration_ms                DOUBLE,
        outcome                    VARCHAR,
        error_type                 VARCHAR,
        error_message              VARCHAR,
        payload_json               JSON
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_csr_tool_call_id ON code_search_runs(tool_call_id)"
    )


def _ensure_code_search_providers(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _CSP_TABLE_NAME,
        """
        terminal_event_id VARCHAR NOT NULL,
        response_index    INTEGER NOT NULL,
        recorded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        provider          VARCHAR NOT NULL,
        hit_count         INTEGER,
        request_count     INTEGER,
        outcome           VARCHAR,
        compiled_queries  VARCHAR[],
        duration_ms       DOUBLE,
        error_type        VARCHAR,
        error_message     VARCHAR,
        payload_json      JSON,
        PRIMARY KEY (terminal_event_id, response_index)
        """,
    )


def _ensure_code_search_diagnostics(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _CSD_TABLE_NAME,
        """
        terminal_event_id   VARCHAR NOT NULL,
        diagnostic_index    INTEGER NOT NULL,
        recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        provider            VARCHAR,
        outcome             VARCHAR,
        failure_kind        VARCHAR,
        message             VARCHAR,
        status_code         INTEGER,
        retry_after_seconds DOUBLE,
        query               VARCHAR,
        details             JSON,
        PRIMARY KEY (terminal_event_id, diagnostic_index)
        """,
    )


def _ensure_code_search_hits(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _CSH_TABLE_NAME,
        """
        terminal_event_id          VARCHAR NOT NULL,
        hit_rank                   INTEGER NOT NULL,
        recorded_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
        url                        VARCHAR,
        repository                 VARCHAR,
        path                       VARCHAR,
        sha                        VARCHAR,
        provider                   VARCHAR,
        query_variant              VARCHAR,
        search_rank                INTEGER,
        result_kind                VARCHAR,
        evidence_role              VARCHAR,
        title                      VARCHAR,
        snippet                    VARCHAR,
        published_date             VARCHAR,
        final_score                DOUBLE,
        score_components           JSON,
        reasons                    VARCHAR[],
        hydrated                   BOOLEAN,
        hydrated_source_truncated  BOOLEAN,
        line_start                 INTEGER,
        line_end                   INTEGER,
        commit_oid                 VARCHAR,
        fragment_count             INTEGER,
        symbol_count               INTEGER,
        match_span_count           INTEGER,
        location_precision         VARCHAR,
        lines_available            BOOLEAN,
        revision_available         BOOLEAN,
        match_data_available       BOOLEAN,
        source_metadata            JSON,
        payload_json               JSON,
        PRIMARY KEY (terminal_event_id, hit_rank)
        """,
    )


def _ensure_code_search_hit_variants(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _CSHV_TABLE_NAME,
        """
        terminal_event_id  VARCHAR NOT NULL,
        hit_rank           INTEGER NOT NULL,
        association_index  INTEGER NOT NULL,
        variant_index      INTEGER,
        provider           VARCHAR,
        query_variant      VARCHAR,
        search_rank        INTEGER,
        PRIMARY KEY (terminal_event_id, hit_rank, association_index)
        """,
    )


def _ensure_code_search_query_variants(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _CSQV_TABLE_NAME,
        """
        terminal_event_id VARCHAR NOT NULL,
        variant_index     INTEGER NOT NULL,
        recorded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        query_text        VARCHAR NOT NULL,
        variant_kind      VARCHAR,
        PRIMARY KEY (terminal_event_id, variant_index)
        """,
    )


def _ensure_code_search_repositories(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _CSREPO_TABLE_NAME,
        """
        terminal_event_id  VARCHAR NOT NULL,
        repository_index   INTEGER NOT NULL,
        recorded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        name_with_owner    VARCHAR,
        url                VARCHAR,
        description        VARCHAR,
        stars              INTEGER,
        forks              INTEGER,
        pushed_at          VARCHAR,
        language           VARCHAR,
        topics             VARCHAR[],
        license_spdx_id    VARCHAR,
        homepage_url       VARCHAR,
        default_branch     VARCHAR,
        head_oid           VARCHAR,
        archived           BOOLEAN,
        fork               BOOLEAN,
        discovery_rank     INTEGER,
        discovery_score    DOUBLE,
        discovery_queries  VARCHAR[],
        proof_hits         INTEGER,
        proof_paths        VARCHAR[],
        proof_providers    VARCHAR[],
        verified           BOOLEAN,
        payload_json       JSON,
        PRIMARY KEY (terminal_event_id, repository_index)
        """,
    )


def _ensure_code_search_rerank(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _CSRERANK_TABLE_NAME,
        """
        terminal_event_id   VARCHAR NOT NULL PRIMARY KEY,
        recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        provider            VARCHAR,
        model               VARCHAR,
        input_count         INTEGER,
        output_count        INTEGER,
        reranked_count      INTEGER,
        status              VARCHAR,
        diagnostic_outcome  VARCHAR,
        diagnostic_message  VARCHAR,
        duration_ms         DOUBLE,
        payload_json        JSON
        """,
    )


# ---------------------------------------------------------------------------
# Content Operations and Summary tables
# ---------------------------------------------------------------------------
def _ensure_content_operations(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _CO_TABLE_NAME,
        """
        terminal_event_id VARCHAR NOT NULL PRIMARY KEY,
        tool_call_id      VARCHAR NOT NULL,
        recorded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        trace_id          VARCHAR,
        session_id        VARCHAR,
        tool_name         VARCHAR NOT NULL,
        input_count       INTEGER,
        output_count      INTEGER,
        duration_ms       DOUBLE,
        status            VARCHAR,
        error_type        VARCHAR,
        error_message     VARCHAR,
        payload_json      JSON
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_co_tool_call_id ON content_operations(tool_call_id)"
    )


def _ensure_content_fetches(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _CF_TABLE_NAME,
        """
        terminal_event_id       VARCHAR NOT NULL,
        tool_call_id            VARCHAR NOT NULL,
        item_index              INTEGER NOT NULL,
        recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
        input_url               VARCHAR,
        normalized_url          VARCHAR,
        fetched_url             VARCHAR,
        source_type             VARCHAR,
        fetch_backend           VARCHAR,
        status                  VARCHAR,
        content_length          INTEGER,
        page_char_count         INTEGER,
        word_count              INTEGER,
        window_offset           INTEGER,
        window_length           INTEGER,
        window_returned_chars   INTEGER,
        window_total_chars      INTEGER,
        window_has_more         BOOLEAN,
        window_next_offset      INTEGER,
        item_duration_ms        DOUBLE,
        payload_json            JSON,
        PRIMARY KEY (terminal_event_id, item_index)
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_cf_tool_call_id ON content_fetches(tool_call_id)"
    )


def _ensure_content_summaries(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _CSUM_TABLE_NAME,
        """
        terminal_event_id        VARCHAR NOT NULL,
        tool_call_id             VARCHAR NOT NULL,
        item_index               INTEGER NOT NULL,
        recorded_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
        normalized_url           VARCHAR,
        focus_query              VARCHAR,
        input_chars              INTEGER,
        source_url_count         INTEGER,
        is_batch                 BOOLEAN,
        batch_size               INTEGER,
        is_stub                  BOOLEAN,
        backend                  VARCHAR,
        model_requested          VARCHAR,
        model_used               VARCHAR,
        fallback_attempted       BOOLEAN,
        fallback_tier            INTEGER,
        input_tokens             INTEGER,
        output_tokens            INTEGER,
        total_tokens             INTEGER,
        summary_length_chars     INTEGER,
        key_points_count         INTEGER,
        important_entities_count INTEGER,
        verbatim_terms_count     INTEGER,
        limitations_count        INTEGER,
        source_date              VARCHAR,
        status                   VARCHAR,
        error_type               VARCHAR,
        error_message            VARCHAR,
        duration_ms              DOUBLE,
        payload_json             JSON,
        PRIMARY KEY (terminal_event_id, item_index)
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_csum_tool_call_id ON content_summaries(tool_call_id)"
    )


def _ensure_content_summary_attempts(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _CSUMA_TABLE_NAME,
        """
        tool_call_id       VARCHAR NOT NULL,
        item_index         INTEGER,
        attempt_index      INTEGER NOT NULL,
        recorded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        is_batch           BOOLEAN,
        batch_size         INTEGER,
        backend            VARCHAR,
        model_requested    VARCHAR,
        model_used         VARCHAR,
        fallback_tier      INTEGER,
        source_url_count   INTEGER,
        input_chars        INTEGER,
        input_tokens       INTEGER,
        output_tokens      INTEGER,
        total_tokens       INTEGER,
        duration_ms        DOUBLE,
        status             VARCHAR,
        error_type         VARCHAR,
        error_message      VARCHAR,
        payload_json       JSON,
        PRIMARY KEY (tool_call_id, attempt_index)
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_csuma_tool_call_id ON content_summary_attempts(tool_call_id)"
    )


# ---------------------------------------------------------------------------
# Web search funnel uplift tables
# ---------------------------------------------------------------------------
def _ensure_result_catalog(connection: duckdb.DuckDBPyConnection) -> None:
    """Cross-run canonical URL registry — one row per unique URL."""
    _create_table(
        connection,
        _RC_CAT_TABLE_NAME,
        """
        canonical_result_id   VARCHAR NOT NULL PRIMARY KEY,
        canonical_url          VARCHAR NOT NULL UNIQUE,
        domain                 VARCHAR NOT NULL,
        title_first_seen       VARCHAR,
        first_seen_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        first_seen_run_key     VARCHAR,
        total_run_appearances  INTEGER DEFAULT 0
        """,
    )


def _ensure_provider_results(connection: duckdb.DuckDBPyConnection) -> None:
    """Per-provider-per-candidate provenance."""
    _create_table(
        connection,
        _PR_TABLE_NAME,
        """
        provider_result_id    VARCHAR NOT NULL PRIMARY KEY,
        provider_call_id      VARCHAR NOT NULL,
        run_key               VARCHAR NOT NULL,
        branch_id             VARCHAR NOT NULL,
        provider              VARCHAR NOT NULL,
        provider_rank         INTEGER NOT NULL,
        canonical_result_id   VARCHAR NOT NULL,
        raw_url               VARCHAR NOT NULL,
        title                 VARCHAR,
        snippet               VARCHAR,
        raw_score             DOUBLE,
        is_eligible           BOOLEAN,
        rejection_reason      VARCHAR,
        recorded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
        payload_json          JSON
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_pr_run_key ON provider_results(run_key)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_pr_provider ON provider_results(provider)"
    )


def _ensure_query_variants(connection: duckdb.DuckDBPyConnection) -> None:
    """Planner query variant lifecycle."""
    _create_table(
        connection,
        _QV_TABLE_NAME,
        """
        variant_id      VARCHAR NOT NULL PRIMARY KEY,
        run_key         VARCHAR NOT NULL,
        variant_order   INTEGER NOT NULL,
        variant_role    VARCHAR NOT NULL,
        query_text      VARCHAR NOT NULL,
        selected        BOOLEAN NOT NULL DEFAULT FALSE,
        executed        BOOLEAN NOT NULL DEFAULT FALSE,
        skip_reason     VARCHAR,
        recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (run_key, variant_order)
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_qv_run_key ON query_variants(run_key)"
    )


def _ensure_candidate_stage_events(connection: duckdb.DuckDBPyConnection) -> None:
    """Per-candidate per-rerank-stage survival tracking."""
    _create_table(
        connection,
        _CSE_TABLE_NAME,
        """
        stage_execution_id   VARCHAR NOT NULL,
        run_key              VARCHAR NOT NULL,
        canonical_result_id VARCHAR NOT NULL,
        entered              BOOLEAN NOT NULL,
        survived             BOOLEAN NOT NULL,
        rank_before          INTEGER,
        rank_after           INTEGER,
        score_before         DOUBLE,
        score_after          DOUBLE,
        score_name           VARCHAR,
        removal_reason       VARCHAR,
        recorded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (stage_execution_id, canonical_result_id),
        payload_json         JSON
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_cse_run_key ON candidate_stage_events(run_key)"
    )


def _ensure_tool_output_items(connection: duckdb.DuckDBPyConnection) -> None:
    """Output items from any tool invocation — links search results to content fetches."""
    _create_table(
        connection,
        _TOI_TABLE_NAME,
        """
        output_item_id       VARCHAR NOT NULL PRIMARY KEY,
        tool_call_id         VARCHAR NOT NULL,
        session_id           VARCHAR,
        run_key              VARCHAR,
        tool_name            VARCHAR NOT NULL,
        item_type            VARCHAR NOT NULL,
        item_rank            INTEGER NOT NULL,
        canonical_result_id VARCHAR,
        raw_url              VARCHAR,
        title                VARCHAR,
        snippet              VARCHAR,
        recorded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tool_call_id, item_type, item_rank)
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_toi_tool_call_id ON tool_output_items(tool_call_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_toi_run_key ON tool_output_items(run_key)"
    )


# ---------------------------------------------------------------------------
# Result labels foundation — human & model annotations for ranking replay
# ---------------------------------------------------------------------------
def _ensure_result_labels(connection: duckdb.DuckDBPyConnection) -> None:
    """Result labels / annotations for search quality evaluation and replay."""
    _create_table(
        connection,
        _RL_TABLE_NAME,
        """
        label_id             VARCHAR NOT NULL PRIMARY KEY,
        recorded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        run_key              VARCHAR NOT NULL,
        position             INTEGER NOT NULL,
        stage                VARCHAR NOT NULL DEFAULT 'final',
        label                DOUBLE NOT NULL,
        canonical_result_id  VARCHAR,
        raw_url              VARCHAR,
        source               VARCHAR NOT NULL DEFAULT 'human',
        annotator_id         VARCHAR,
        rubric_version       VARCHAR NOT NULL DEFAULT 'v1',
        discounted_gain      DOUBLE,
        notes                VARCHAR,
        payload_json         JSON
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_rl_run_key ON result_labels(run_key)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_rl_canonical_result_id ON result_labels(canonical_result_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_rl_source ON result_labels(source)"
    )


# ---------------------------------------------------------------------------
# vss extension — HNSW vector indexes on embedding tables
# ---------------------------------------------------------------------------
_vss_installed = False


def ensure_vss_loaded(connection: duckdb.DuckDBPyConnection) -> bool:
    """Load vss extension and enable HNSW persistence on this connection."""
    try:
        connection.execute("LOAD vss;")
        connection.execute("SET hnsw_enable_experimental_persistence = true;")
        return True
    except Exception:
        return False


def ensure_vss_extension(connection: duckdb.DuckDBPyConnection) -> None:
    """Install/load vss, create HNSW indexes on embedding tables."""
    global _vss_installed
    try:
        if not _vss_installed:
            try:
                connection.execute("INSTALL vss;")
            except Exception:
                pass
            _vss_installed = True
        ensure_vss_loaded(connection)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_qemb_hnsw ON query_embeddings USING HNSW (embedding);"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_cemb_hnsw "
            "ON candidate_embeddings USING HNSW (embedding);"
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "vss extension unavailable — embedding tables work without HNSW "
            "(similarity search falls back to brute-force array_distance scan). "
            "Error: %s",
            exc,
        )


# ---------------------------------------------------------------------------
# Public entrypoints
# ---------------------------------------------------------------------------
def ensure_store_schema(*, db_path: str | None = None) -> None:
    """Create all pipeline + embedding + health tables if absent."""
    from ...settings import settings

    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # FlockMTL INSTALL + LOAD is a network operation that writes only to
    # DuckDB's local extension cache (~/.duckdb/), not the user database.
    # Do it on a short-lived connection OUTSIDE the writer lock so it
    # doesn't block concurrent analytics writers during a cold community
    # catalog fetch (can take 30s+).
    flockmtl_install_ok = False
    flockmtl_loaded = False
    prelock_connection: duckdb.DuckDBPyConnection | None = None
    if settings.flockmtl_enabled:
        # INSTALL the extension exactly once per process (cached), then
        # LOAD on a short-lived connection OUTSIDE the writer lock so a
        # cold community catalog fetch (30s+) doesn't block concurrent
        # analytics writers. INSTALL and LOAD target DuckDB's local
        # extension cache, not the user database. If either step fails
        # (e.g. network unreachable), skip Flock entirely — judges will
        # silently no-op until a later bootstrap succeeds.
        flockmtl_install_ok = _install_flockmtl_once()
    if flockmtl_install_ok:
        prelock_connection = duckdb.connect(str(path))
        try:
            flockmtl_loaded = bool(ensure_flockmtl_loaded(prelock_connection))
        finally:
            prelock_connection.close()

    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_search_runs(connection)
            _ensure_search_branches(connection)
            _ensure_provider_calls(connection)
            _ensure_columns(
                connection,
                "provider_calls",
                {
                    "request_query": "VARCHAR",
                    "request_url": "VARCHAR",
                    "http_status": "INTEGER",
                    "result_class": "VARCHAR",
                    "response_meta_json": "JSON",
                    "retry_after_seconds": "DOUBLE",
                    "retryable": "BOOLEAN",
                },
            )
            _ensure_search_candidates(connection)
            _ensure_rerank_stages(connection)
            _ensure_rerank_candidates(connection)
            _ensure_final_results(connection)
            _ensure_query_embeddings(connection)
            _ensure_candidate_embeddings(connection)
            _ensure_provider_health_transitions(connection)
            _ensure_llm_call_log(connection)
            _ensure_tool_calls(connection)
            _ensure_query_understanding_events(connection)
            _ensure_llm_judgments(connection)
            _ensure_columns(
                connection,
                "llm_judgments",
                {
                    "facet": "VARCHAR",
                    "reasoning": "VARCHAR",
                    "rubric_version": "VARCHAR DEFAULT 'v1'",
                    "confidence": "SMALLINT",
                    "context_shown": "JSON",
                },
            )
            _ensure_judge_rubrics(connection)
            _ensure_judge_calibration_set(connection)
            _ensure_search_quality_scores(connection)
            _ensure_judge_evaluations(connection)
            _ensure_columns(
                connection,
                "judge_evaluations",
                {
                    "relevance_raw": "INTEGER",
                    "relevance_scale": "VARCHAR",
                    "status": "VARCHAR DEFAULT 'success'",
                    "error_type": "VARCHAR",
                    "error_message": "VARCHAR",
                },
            )
            # Phase 1: Additive stable IDs on existing tables
            _ensure_columns(
                connection,
                "search_branches",
                {"branch_id": "VARCHAR"},
            )
            _ensure_columns(
                connection,
                "provider_calls",
                {"provider_call_id": "VARCHAR"},
            )
            _ensure_columns(
                connection,
                "search_candidates",
                {"canonical_result_id": "VARCHAR"},
            )
            _ensure_columns(
                connection,
                "tool_calls",
                {"run_key": "VARCHAR"},
            )
            _ensure_summary_intent_daily(connection)
            _ensure_summary_provider_daily(connection)
            _ensure_summary_quality_daily(connection)
            _ensure_summary_rerank_daily(connection)
            _ensure_quick_web_search_runs(connection)
            _ensure_quick_web_search_citations(connection)
            _ensure_gemini_search_runs(connection)
            _ensure_gemini_search_sources(connection)
            _ensure_gemini_search_attempts(connection)
            _ensure_code_search_runs(connection)
            _ensure_code_search_providers(connection)
            _ensure_code_search_diagnostics(connection)
            _ensure_code_search_hits(connection)
            _ensure_code_search_hit_variants(connection)
            _ensure_code_search_query_variants(connection)
            _ensure_code_search_repositories(connection)
            _ensure_code_search_rerank(connection)
            _ensure_content_operations(connection)
            _ensure_content_fetches(connection)
            _ensure_content_summaries(connection)
            _ensure_content_summary_attempts(connection)
            # Phase 2: Web search funnel uplift tables
            _ensure_result_catalog(connection)
            _ensure_provider_results(connection)
            _ensure_query_variants(connection)
            _ensure_candidate_stage_events(connection)
            _ensure_tool_output_items(connection)
            _ensure_result_labels(connection)
            _ensure_flockmtl_resources_table(connection)
            if settings.vss_enabled:
                ensure_vss_extension(connection)
            if flockmtl_loaded:
                ensure_flockmtl_resources(connection)
        finally:
            connection.close()


def ensure_search_quality_tables(*, db_path: str | None = None) -> None:
    """Ensure all tables needed by quality scoring and judge writes exist."""
    ensure_store_schema(db_path=db_path)

from .ab_schema import (  # noqa: E402
    _ensure_ab_assignments,  # noqa: F401
    _ensure_ab_experiment_variants,  # noqa: F401
    _ensure_ab_experiments,  # noqa: F401
    _ensure_ab_results,  # noqa: F401
    _ensure_ab_shadow_runs,  # noqa: F401
)
from .summary_schema import (  # noqa: E402
    _ensure_summary_intent_daily,  # noqa: F401
    _ensure_summary_provider_daily,  # noqa: F401
    _ensure_summary_quality_daily,  # noqa: F401
    _ensure_summary_rerank_daily,  # noqa: F401
)
