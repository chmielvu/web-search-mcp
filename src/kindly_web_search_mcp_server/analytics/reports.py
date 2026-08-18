"""Deterministic read-only DuckDB analytics reports."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import duckdb
import pyarrow as pa

from ..settings import settings


def _db_path(db_path: str | None = None) -> Path:
    return Path(db_path or settings.analytics_duckdb_path)


def _run(sql: str, *, db_path: str | None = None) -> pa.Table:
    path = _db_path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"Analytics DuckDB file does not exist: {path}")

    connection = duckdb.connect(str(path), read_only=True)
    try:
        return connection.execute(sql).to_arrow_table()
    finally:
        connection.close()


def provider_performance(*, days: int = 7, db_path: str | None = None) -> pa.Table:
    """Per-provider call stats: volume, success rate, latency percentiles."""
    window = max(1, int(days))
    sql = f"""
        SELECT
            provider,
            COUNT(*) AS total_calls,
            COUNT(*) FILTER (WHERE status = 'success') AS success_count,
            COUNT(*) FILTER (WHERE error_type IS NOT NULL) AS error_count,
            ROUND(100.0
                * COUNT(*) FILTER (WHERE status = 'success')
                / NULLIF(COUNT(*), 0), 1) AS success_rate_pct,
            ROUND(AVG(latency_ms), 0) AS avg_latency_ms,
            ROUND(quantile_cont(latency_ms, 0.50), 0) AS p50_latency_ms,
            ROUND(quantile_cont(latency_ms, 0.95), 0) AS p95_latency_ms,
            SUM(num_results_returned) AS total_results_returned,
            MODE(error_type) AS most_common_error
        FROM provider_calls
        WHERE recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        GROUP BY provider
        ORDER BY total_calls DESC, provider
    """
    return _run(sql, db_path=db_path)


def rewrite_effectiveness(*, days: int = 7, db_path: str | None = None) -> pa.Table:
    """Rewrite usage rates and efficiency: adoption, latency, token usage."""
    window = max(1, int(days))
    sql = f"""
        SELECT
            rewrite_enabled,
            COALESCE(rewrite_model, 'none') AS rewrite_model,
            COALESCE(intent, 'unknown') AS intent,
            COUNT(*) AS runs,
            ROUND(AVG(rewrite_latency_ms), 1) AS avg_rewrite_latency_ms,
            ROUND(quantile_cont(rewrite_latency_ms, 0.95), 1) AS p95_rewrite_latency_ms,
            AVG(rewrite_input_tokens) AS avg_input_tokens,
            AVG(rewrite_output_tokens) AS avg_output_tokens,
            COUNT(*) FILTER (WHERE rewrite_error IS NOT NULL) AS rewrite_errors,
            ROUND(100.0
                * COUNT(*) FILTER (WHERE rewrite_error IS NOT NULL)
                / NULLIF(COUNT(*), 0), 2) AS rewrite_error_rate_pct
        FROM search_runs
        WHERE recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        GROUP BY rewrite_enabled, rewrite_model, intent
        ORDER BY runs DESC, rewrite_enabled DESC, rewrite_model, intent
    """
    return _run(sql, db_path=db_path)


def error_taxonomy(*, days: int = 7, db_path: str | None = None) -> pa.Table:
    """Errors from provider_calls + rerank_stages, tagged by provider/stage."""
    window = max(1, int(days))
    sql = f"""
        SELECT
            source,
            provider,
            stage,
            error_type,
            COUNT(*) AS errors,
            ROUND(AVG(duration_ms), 1) AS avg_duration_ms,
            COUNT(DISTINCT run_key) AS affected_runs
        FROM (
            SELECT
                'provider_call' AS source,
                provider,
                'provider_call' AS stage,
                COALESCE(error_type, 'unknown') AS error_type,
                latency_ms AS duration_ms,
                run_key
            FROM provider_calls
            WHERE error_type IS NOT NULL
              AND recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
            UNION ALL
            SELECT
                'rerank_stage' AS source,
                COALESCE(provider, 'unknown') AS provider,
                COALESCE(stage, 'unknown') AS stage,
                COALESCE(error_type, 'unknown') AS error_type,
                duration_ms,
                run_key
            FROM rerank_stages
            WHERE error_type IS NOT NULL
              AND recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        ) t
        GROUP BY source, provider, stage, error_type
        ORDER BY errors DESC, source, provider, stage, error_type
    """
    return _run(sql, db_path=db_path)


def candidate_survival(*, days: int = 7, db_path: str | None = None) -> pa.Table:
    """Funnel: provider_call candidates → merged candidates → final results."""
    window = max(1, int(days))
    sql = f"""
        WITH stage_rows AS (
            SELECT
                'provider_call' AS stage,
                p.recorded_at,
                p.run_key,
                urls.url
            FROM provider_calls AS p,
                 UNNEST(p.candidate_urls) AS urls(url)
            WHERE p.recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
            UNION ALL
            SELECT
                'merged' AS stage,
                recorded_at,
                run_key,
                link AS url
            FROM search_candidates
            WHERE recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
            UNION ALL
            SELECT
                'final' AS stage,
                recorded_at,
                run_key,
                link AS url
            FROM final_results
            WHERE recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        )
        SELECT
            stage,
            COUNT(*) AS rows,
            COUNT(DISTINCT run_key) AS runs,
            COUNT(DISTINCT url) AS unique_urls
        FROM stage_rows
        GROUP BY 1
        ORDER BY CASE stage
            WHEN 'provider_call' THEN 1
            WHEN 'merged' THEN 2
            WHEN 'final' THEN 3
            ELSE 99
        END
    """
    return _run(sql, db_path=db_path)


def eval_quality_summary(*, days: int = 30, db_path: str | None = None) -> pa.Table:
    """Eval quality aggregated per suite/tool from vw_eval_provider_quality."""
    window = max(1, int(days))
    sql = f"""
        SELECT
            suite_name,
            target_tool,
            COUNT(*) AS cases,
            SUM(passes) AS passes,
            SUM(fails) AS fails,
            ROUND(100.0 * SUM(passes) / NULLIF(SUM(passes) + SUM(fails), 0), 1) AS pass_rate_pct,
            ROUND(AVG(avg_score), 3) AS avg_score
        FROM vw_eval_provider_quality
        WHERE eval_run_id IN (
            SELECT eval_run_id
            FROM eval_runs
            WHERE created_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        )
        GROUP BY suite_name, target_tool
        ORDER BY cases DESC, suite_name, target_tool
    """
    return _run(sql, db_path=db_path)


def latency_breakdown(*, days: int = 7, db_path: str | None = None) -> pa.Table:
    """Per-stage latency waterfall: rewrite → provider → merge → rerank."""
    window = max(1, int(days))
    sql = f"""
        WITH run_timings AS (
            SELECT
                sr.run_key,
                sr.recorded_at,
                sr.duration_ms AS total_duration_ms,
                sr.branch_count,
                sr.status,
                -- rewrite stage (0 if skipped)
                COALESCE(sr.rewrite_latency_ms, 0) AS rewrite_latency_ms,
                -- merge stage (approximate: sr.merged_count rows processed)
                0.0 AS merge_latency_ms,
                -- rerank stages aggregated
                (SELECT SUM(COALESCE(rs.duration_ms, 0))
                 FROM rerank_stages rs
                 WHERE rs.run_key = sr.run_key) AS rerank_latency_ms,
                -- provider stage residual (total - everything else)
                GREATEST(sr.duration_ms
                    - COALESCE(sr.rewrite_latency_ms, 0)
                    - (SELECT SUM(COALESCE(rs.duration_ms, 0))
                       FROM rerank_stages rs
                       WHERE rs.run_key = sr.run_key),
                    0) AS provider_latency_ms
            FROM search_runs sr
            WHERE sr.recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
              AND sr.status = 'success'
        ),
        stage_samples AS (
            SELECT run_key, recorded_at, status, branch_count,
                   rewrite_latency_ms, provider_latency_ms,
                   merge_latency_ms, rerank_latency_ms,
                   total_duration_ms
            FROM run_timings
        )
        SELECT * FROM (
            SELECT
                'total' AS stage,
                COUNT(*) AS runs,
                ROUND(AVG(total_duration_ms), 1) AS avg_duration_ms,
                ROUND(quantile_cont(total_duration_ms, 0.50), 1) AS p50_ms,
                ROUND(quantile_cont(total_duration_ms, 0.95), 1) AS p95_ms
            FROM stage_samples
            UNION ALL
            SELECT
                'rewrite' AS stage,
                COUNT(*) FILTER (WHERE rewrite_latency_ms > 0) AS runs,
                ROUND(AVG(rewrite_latency_ms) FILTER (WHERE rewrite_latency_ms > 0), 1) AS avg_duration_ms,
                ROUND(quantile_cont(rewrite_latency_ms, 0.50), 1) AS p50_ms,
                ROUND(quantile_cont(rewrite_latency_ms, 0.95), 1) AS p95_ms
            FROM stage_samples
            UNION ALL
            SELECT
                'provider_fetch' AS stage,
                COUNT(*) AS runs,
                ROUND(AVG(provider_latency_ms), 1) AS avg_duration_ms,
                ROUND(quantile_cont(provider_latency_ms, 0.50), 1) AS p50_ms,
                ROUND(quantile_cont(provider_latency_ms, 0.95), 1) AS p95_ms
            FROM stage_samples
            UNION ALL
            SELECT
                'merge_dedup' AS stage,
                COUNT(*) FILTER (WHERE merge_latency_ms > 0) AS runs,
                ROUND(AVG(merge_latency_ms) FILTER (WHERE merge_latency_ms > 0), 1) AS avg_duration_ms,
                ROUND(quantile_cont(merge_latency_ms, 0.50), 1) AS p50_ms,
                ROUND(quantile_cont(merge_latency_ms, 0.95), 1) AS p95_ms
            FROM stage_samples
            UNION ALL
            SELECT
                'rerank' AS stage,
                COUNT(*) FILTER (WHERE rerank_latency_ms > 0) AS runs,
                ROUND(AVG(rerank_latency_ms) FILTER (WHERE rerank_latency_ms > 0), 1) AS avg_duration_ms,
                ROUND(quantile_cont(rerank_latency_ms, 0.50), 1) AS p50_ms,
                ROUND(quantile_cont(rerank_latency_ms, 0.95), 1) AS p95_ms
            FROM stage_samples
        ) AS sub
        ORDER BY CASE stage
            WHEN 'total' THEN 1
            WHEN 'rewrite' THEN 2
            WHEN 'provider_fetch' THEN 3
            WHEN 'merge_dedup' THEN 4
            WHEN 'rerank' THEN 5
            ELSE 99
        END
    """
    return _run(sql, db_path=db_path)


def provider_final_contribution(*, days: int = 7, db_path: str | None = None) -> pa.Table:
    """Which providers survive into final_results and how often."""
    window = max(1, int(days))
    sql = f"""
        WITH provider_results AS (
            SELECT
                run_key,
                provider,
                COUNT(*) AS result_count,
                SUM(provider_count) AS total_provider_count
            FROM final_results,
                 UNNEST(providers) AS t(provider)
            WHERE recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
            GROUP BY run_key, provider
        ),
        run_totals AS (
            SELECT
                run_key,
                SUM(result_count) AS results_in_run
            FROM provider_results
            GROUP BY run_key
        )
        SELECT
            pr.provider,
            COUNT(*) AS runs_appeared,
            SUM(pr.result_count) AS total_results,
            ROUND(100.0 * SUM(pr.result_count) / NULLIF(SUM(rt.results_in_run), 0), 1) AS avg_share_pct,
            COUNT(*) FILTER (WHERE pr.result_count = 1) AS sole_result_count,
            COUNT(*) FILTER (WHERE pr.result_count > 1) AS multi_result_count,
            ROUND(AVG(pr.result_count), 2) AS avg_results_per_run
        FROM provider_results pr
        JOIN run_totals rt ON rt.run_key = pr.run_key
        GROUP BY pr.provider
        ORDER BY total_results DESC, pr.provider
    """
    return _run(sql, db_path=db_path)


def provider_reliability(*, days: int = 7, db_path: str | None = None) -> pa.Table:
    """Provider yield and typed failure classes with explicit denominators."""
    window = max(1, int(days))
    sql = f"""
        SELECT
            provider,
            COUNT(*) AS calls,
            COUNT(*) FILTER (WHERE result_class = 'nonempty') AS nonempty_calls,
            COUNT(*) FILTER (WHERE result_class = 'empty') AS empty_calls,
            COUNT(*) FILTER (WHERE result_class = 'error') AS error_calls,
            COUNT(*) FILTER (WHERE result_class = 'timeout') AS timeout_calls,
            COUNT(*) FILTER (WHERE result_class = 'incomplete') AS incomplete_calls,
            ROUND(100.0 * COUNT(*) FILTER (WHERE result_class = 'nonempty')
                / NULLIF(COUNT(*), 0), 1) AS nonempty_rate_pct,
            ROUND(quantile_cont(latency_ms, 0.50), 0) AS p50_latency_ms,
            ROUND(quantile_cont(latency_ms, 0.95), 0) AS p95_latency_ms,
            MODE(error_type) AS common_error
        FROM provider_calls
        WHERE recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        GROUP BY provider
        ORDER BY calls DESC, provider
    """
    return _run(sql, db_path=db_path)


def quality_misses(*, days: int = 7, db_path: str | None = None) -> pa.Table:
    """Result-quality misses by intent, rank, provenance, and classifier confidence."""
    window = max(1, int(days))
    sql = f"""
        WITH rows AS (
            SELECT
                lj.recorded_at,
                sr.intent,
                sr.understanding_confidence AS classifier_confidence,
                fr.rank,
                array_to_string(fr.providers, ',') AS provider_group,
                try_cast(json_extract(lj.payload_json, '$.parsed.intent_match') AS BOOLEAN) AS intent_match,
                try_cast(json_extract(lj.payload_json, '$.parsed.informativeness') AS INTEGER) AS informativeness,
                lj.status AS judge_status
            FROM llm_judgments lj
            LEFT JOIN search_runs sr ON sr.run_key = lj.run_key
            LEFT JOIN final_results fr
                ON fr.run_key = lj.run_key AND fr.link = lj.judgment_target
            WHERE lj.judgment_kind = 'result_quality'
              AND lj.recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        )
        SELECT
            COALESCE(intent, 'unknown') AS intent,
            COALESCE(provider_group, 'unknown') AS provider_group,
            CASE
                WHEN rank IS NULL THEN 'unknown'
                WHEN rank <= 3 THEN '1-3'
                WHEN rank <= 10 THEN '4-10'
                ELSE '11+'
            END AS rank_bucket,
            CASE
                WHEN classifier_confidence IS NULL THEN 'unknown'
                WHEN classifier_confidence < 0.50 THEN '0.00-0.49'
                WHEN classifier_confidence < 0.75 THEN '0.50-0.74'
                WHEN classifier_confidence < 0.90 THEN '0.75-0.89'
                ELSE '0.90-1.00'
            END AS confidence_bucket,
            COUNT(*) AS judged_results,
            COUNT(*) FILTER (WHERE intent_match = false) AS intent_misses,
            COUNT(*) FILTER (WHERE informativeness <= 2) AS low_informativeness,
            COUNT(*) FILTER (WHERE judge_status <> 'success') AS judge_errors
        FROM rows
        GROUP BY ALL
        ORDER BY judged_results DESC, intent, provider_group, rank_bucket
    """
    return _run(sql, db_path=db_path)


def classifier_calibration(*, days: int = 30, db_path: str | None = None) -> pa.Table:
    """Confidence distribution; unlabeled rows never become fake calibration labels."""
    window = max(1, int(days))
    sql = f"""
        SELECT
            CASE
                WHEN final_confidence < 0.50 THEN '0.00-0.49'
                WHEN final_confidence < 0.75 THEN '0.50-0.74'
                WHEN final_confidence < 0.90 THEN '0.75-0.89'
                ELSE '0.90-1.00'
            END AS confidence_bucket,
            decision_path,
            'unlabeled' AS label_source,
            COUNT(*) AS events,
            ROUND(AVG(final_confidence), 3) AS avg_confidence,
            CAST(NULL AS DOUBLE) AS observed_agreement,
            CAST(NULL AS DOUBLE) AS brier_score
        FROM query_understanding_events
        WHERE recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        GROUP BY ALL
        ORDER BY confidence_bucket, decision_path
    """
    return _run(sql, db_path=db_path)


def tool_call_coverage(*, days: int = 7, db_path: str | None = None) -> pa.Table:
    """Cross-tool coverage: event volumes, terminal rates, duration percentiles."""
    window = max(1, int(days))
    sql = f"""
        SELECT
            tool_name,
            COUNT(*) AS total_events,
            COUNT(*) FILTER (WHERE phase = 'request') AS request_events,
            COUNT(*) FILTER (WHERE phase = 'response') AS response_events,
            COUNT(*) FILTER (WHERE phase = 'error') AS error_events,
            COUNT(DISTINCT tool_call_id) AS distinct_tool_calls,
            ROUND(100.0 * COUNT(*) FILTER (WHERE phase IN ('response', 'error'))
                / NULLIF(COUNT(*) FILTER (WHERE phase = 'request'), 0), 2) AS terminal_event_rate_pct,
            ROUND(AVG(duration_ms) FILTER (WHERE duration_ms IS NOT NULL), 2) AS avg_duration_ms,
            ROUND(quantile_cont(duration_ms, 0.95) FILTER (WHERE duration_ms IS NOT NULL), 2) AS p95_duration_ms
        FROM tool_calls
        WHERE recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        GROUP BY tool_name
        ORDER BY total_events DESC
    """
    return _run(sql, db_path=db_path)


def quick_search_performance(*, days: int = 7, db_path: str | None = None) -> pa.Table:
    """Quick web search performance: volumes, citations, durations by client model."""
    window = max(1, int(days))
    sql = f"""
        SELECT
            COALESCE(client_model, 'unspecified') AS client_model,
            status,
            COUNT(*) AS total_runs,
            ROUND(AVG(total_citations), 2) AS avg_citations,
            SUM(total_citations) AS total_citations,
            COUNT(*) FILTER (WHERE warnings IS NOT NULL AND json_array_length(warnings) > 0) AS runs_with_warnings,
            ROUND(AVG(duration_ms), 2) AS avg_duration_ms,
            ROUND(quantile_cont(duration_ms, 0.95), 2) AS p95_duration_ms
        FROM quick_web_search_runs
        WHERE recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        GROUP BY client_model, status
        ORDER BY total_runs DESC
    """
    return _run(sql, db_path=db_path)


def gemini_search_performance(*, days: int = 7, db_path: str | None = None) -> pa.Table:
    """Gemini grounded search: token usage, grounding chunks, latency percentiles."""
    window = max(1, int(days))
    sql = f"""
        SELECT
            COALESCE(model_used, 'unknown') AS model_used,
            COALESCE(mode, 'standard') AS mode,
            status,
            COUNT(*) AS total_runs,
            ROUND(AVG(grounding_chunks_count), 2) AS avg_grounding_chunks,
            ROUND(AVG(web_search_queries_count), 2) AS avg_web_search_queries,
            ROUND(AVG(prompt_tokens), 1) AS avg_prompt_tokens,
            ROUND(AVG(completion_tokens), 1) AS avg_completion_tokens,
            ROUND(AVG(total_tokens), 1) AS avg_total_tokens,
            ROUND(AVG(duration_ms), 2) AS avg_duration_ms,
            ROUND(quantile_cont(duration_ms, 0.95), 2) AS p95_duration_ms
        FROM gemini_search_runs
        WHERE recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        GROUP BY model_used, mode, status
        ORDER BY total_runs DESC
    """
    return _run(sql, db_path=db_path)


def code_search_provider_yield(*, days: int = 7, db_path: str | None = None) -> pa.Table:
    """Code search provider yield: responses, hits, requests, and latency."""
    window = max(1, int(days))
    sql = f"""
        SELECT
            provider,
            outcome,
            COUNT(*) AS total_responses,
            SUM(hit_count) AS total_hits_returned,
            ROUND(AVG(hit_count), 2) AS avg_hits_per_response,
            SUM(request_count) AS total_requests,
            ROUND(AVG(duration_ms) FILTER (WHERE duration_ms IS NOT NULL), 2) AS avg_duration_ms
        FROM code_search_providers
        WHERE recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        GROUP BY provider, outcome
        ORDER BY total_responses DESC, provider
    """
    return _run(sql, db_path=db_path)


def code_search_hit_sources(*, days: int = 7, db_path: str | None = None) -> pa.Table:
    """Code search hits: provider breakdown, evidence roles, hydration, scores."""
    window = max(1, int(days))
    sql = f"""
        SELECT
            COALESCE(provider, 'unknown') AS provider,
            COALESCE(result_kind, 'unknown') AS result_kind,
            COALESCE(evidence_role, 'unknown') AS evidence_role,
            COUNT(*) AS total_hits,
            COUNT(*) FILTER (WHERE hydrated = TRUE) AS hydrated_hits,
            ROUND(AVG(final_score), 4) AS avg_final_score,
            ROUND(AVG(fragment_count), 2) AS avg_fragments,
            ROUND(AVG(symbol_count), 2) AS avg_symbols
        FROM code_search_hits
        WHERE recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        GROUP BY provider, result_kind, evidence_role
        ORDER BY total_hits DESC
    """
    return _run(sql, db_path=db_path)


def content_fetch_performance(*, days: int = 7, db_path: str | None = None) -> pa.Table:
    """Content fetch performance: backends, source types, length, word counts."""
    window = max(1, int(days))
    sql = f"""
        SELECT
            cf.fetch_backend,
            cf.source_type,
            cf.status AS fetch_status,
            COUNT(*) AS total_fetches,
            ROUND(AVG(cf.content_length), 0) AS avg_content_length,
            ROUND(AVG(cf.page_char_count), 0) AS avg_page_char_count,
            ROUND(AVG(cf.word_count), 0) AS avg_word_count,
            COUNT(*) FILTER (WHERE cf.window_has_more = TRUE) AS truncated_windows_count
        FROM content_fetches cf
        WHERE cf.recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        GROUP BY cf.fetch_backend, cf.source_type, cf.status
        ORDER BY total_fetches DESC
    """
    return _run(sql, db_path=db_path)


def content_summary_output_signals(*, days: int = 7, db_path: str | None = None) -> pa.Table:
    """Content summary output shape signals: length, entities, key points, tokens."""
    window = max(1, int(days))
    sql = f"""
        SELECT
            COALESCE(backend, 'unspecified') AS backend,
            COALESCE(model_used, 'unspecified') AS model_used,
            is_batch,
            COUNT(*) AS total_summaries,
            ROUND(AVG(summary_length_chars), 0) AS avg_summary_chars,
            ROUND(AVG(key_points_count), 2) AS avg_key_points,
            ROUND(AVG(important_entities_count), 2) AS avg_entities,
            ROUND(AVG(input_tokens) FILTER (WHERE input_tokens IS NOT NULL), 1) AS avg_input_tokens,
            ROUND(AVG(output_tokens) FILTER (WHERE output_tokens IS NOT NULL), 1) AS avg_output_tokens,
            ROUND(AVG(duration_ms) FILTER (WHERE duration_ms IS NOT NULL), 2) AS avg_duration_ms
        FROM content_summaries
        WHERE recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        GROUP BY backend, model_used, is_batch
        ORDER BY total_summaries DESC
    """
    return _run(sql, db_path=db_path)


_REPORTS: dict[str, Callable[..., pa.Table]] = {
    "provider-performance": provider_performance,
    "rewrite-effectiveness": rewrite_effectiveness,
    "error-taxonomy": error_taxonomy,
    "candidate-survival": candidate_survival,
    "eval-quality-summary": eval_quality_summary,
    "latency-breakdown": latency_breakdown,
    "provider-final-contribution": provider_final_contribution,
    "provider-reliability": provider_reliability,
    "quality-misses": quality_misses,
    "classifier-calibration": classifier_calibration,
    "tool-call-coverage": tool_call_coverage,
    "quick-search-performance": quick_search_performance,
    "gemini-search-performance": gemini_search_performance,
    "code-search-provider-yield": code_search_provider_yield,
    "code-search-hit-sources": code_search_hit_sources,
    "content-fetch-performance": content_fetch_performance,
    "content-summary-output-signals": content_summary_output_signals,
}

def available_reports() -> tuple[str, ...]:
    return tuple(sorted(_REPORTS))


def run_report(
    report_name: str,
    *,
    days: int = 7,
    db_path: str | None = None,
) -> pa.Table:
    try:
        report_fn = _REPORTS[report_name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown analytics report {report_name!r}. "
            f"Available reports: {', '.join(available_reports())}."
        ) from exc
    return report_fn(days=days, db_path=db_path)
