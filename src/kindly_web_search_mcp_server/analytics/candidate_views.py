"""Candidate and derived analytics view SQL."""

from __future__ import annotations

from .candidate_survival_views import build_candidate_survival_view_sql


def build_candidate_view_sql(
    target: str,
    *,
    source_table: str = "analytics_event_raw",
) -> list[str]:
    """Build candidate, fetch, answer, and survival views."""
    source = f"{target}.{source_table}"
    return [
        f"""
        CREATE OR REPLACE VIEW {target}.vw_merged_results AS
        SELECT
            e.event_id,
            e.recorded_at,
            coalesce(e.run_key, e.trace_id, e.event_id) AS run_key,
            e.event_name,
            json_extract_string(e.payload_json, '$.query') AS query,
            CAST(r.key AS INTEGER) AS result_index,
            json_extract_string(r.value, '$.title') AS title,
            coalesce(
                json_extract_string(r.value, '$.link'),
                json_extract_string(r.value, '$.url')
            ) AS url,
            json_extract_string(r.value, '$.snippet') AS snippet,
            json_extract_string(r.value, '$.domain') AS domain,
            json_extract(r.value, '$.providers') AS providers_json,
            CAST(json_extract_string(r.value, '$.provider_count') AS INTEGER) AS provider_count,
            CAST(json_extract_string(r.value, '$.score') AS DOUBLE) AS score
        FROM {source} AS e,
             json_each(json_extract(e.payload_json, '$.merged_results')) AS r
        WHERE e.event_name = 'search.orchestrator.response'
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_rewrite_variants AS
        SELECT
            e.event_id,
            e.recorded_at,
            coalesce(e.run_key, e.trace_id, e.event_id) AS run_key,
            e.query,
            CAST(v.key AS INTEGER) AS variant_rank,
            json_extract_string(v.value, '$.kind') AS kind,
            json_extract_string(v.value, '$.target') AS target,
            json_extract_string(v.value, '$.query') AS rewritten_query,
            json_extract_string(v.value, '$.why') AS why,
            CAST(json_extract_string(v.value, '$.weight') AS DOUBLE) AS weight
        FROM {source} AS e,
             json_each(json_extract(e.payload_json, '$.variants')) AS v
        WHERE e.event_name = 'query.rewrite.completed'
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_search_results AS
        SELECT
            e.event_id,
            e.recorded_at,
            coalesce(e.run_key, e.trace_id, e.event_id) AS run_key,
            e.event_name,
            e.query,
            CAST(r.key AS INTEGER) AS result_rank,
            json_extract_string(r.value, '$.title') AS title,
            json_extract_string(r.value, '$.link') AS url,
            json_extract_string(r.value, '$.snippet') AS snippet,
            json_extract_string(r.value, '$.domain') AS domain,
            json_extract(r.value, '$.providers') AS providers_json,
            CAST(json_extract_string(r.value, '$.provider_count') AS INTEGER) AS provider_count,
            CAST(json_extract_string(r.value, '$.score') AS DOUBLE) AS score
        FROM {source} AS e,
             json_each(json_extract(e.payload_json, '$.results')) AS r
        WHERE e.event_name IN (
            'search.orchestrator.response',
            'search.single_query.response',
            'tool.web_search.response'
        )
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_rerank_results AS
        SELECT
            e.event_id,
            e.recorded_at,
            coalesce(e.run_key, e.trace_id, e.event_id) AS run_key,
            e.event_name,
            e.query,
            e.provider,
            e.model,
            CAST(r.key AS INTEGER) AS rerank_rank,
            json_extract_string(r.value, '$.title') AS title,
            json_extract_string(r.value, '$.link') AS url,
            json_extract_string(r.value, '$.snippet') AS snippet,
            json_extract_string(r.value, '$.domain') AS domain,
            json_extract(r.value, '$.providers') AS providers_json,
            CAST(json_extract_string(r.value, '$.provider_count') AS INTEGER) AS provider_count,
            CAST(json_extract_string(r.value, '$.score') AS DOUBLE) AS score
        FROM {source} AS e,
             json_each(json_extract(e.payload_json, '$.results')) AS r
        WHERE e.event_name = 'search.rerank.summary'
          AND json_extract(e.payload_json, '$.results') IS NOT NULL
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_fetch_events AS
        SELECT
            event_id,
            recorded_at,
            coalesce(run_key, trace_id, event_id) AS run_key,
            event_name,
            json_extract_string(payload_json, '$.input_url') AS input_url,
            json_extract_string(payload_json, '$.normalized_url') AS normalized_url,
            json_extract_string(payload_json, '$.fetched_url') AS fetched_url,
            json_extract_string(payload_json, '$.status') AS status,
            json_extract_string(payload_json, '$.source_type') AS source_type,
            json_extract_string(payload_json, '$.fetch_backend') AS fetch_backend,
            json_extract_string(payload_json, '$.content_type') AS content_type,
            json_extract_string(payload_json, '$.page_content') AS page_content,
            length(coalesce(json_extract_string(payload_json, '$.page_content'), '')) AS page_char_count,
            CAST(json_extract_string(payload_json, '$.word_count') AS INTEGER) AS word_count,
            CAST(json_extract_string(payload_json, '$.window.offset') AS INTEGER) AS window_offset,
            CAST(json_extract_string(payload_json, '$.window.length') AS INTEGER) AS window_length,
            CAST(json_extract_string(payload_json, '$.window.returned_chars') AS INTEGER) AS window_returned_chars,
            CAST(json_extract_string(payload_json, '$.window.total_chars') AS INTEGER) AS window_total_chars,
            CAST(json_extract_string(payload_json, '$.window.has_more') AS BOOLEAN) AS window_has_more,
            CAST(json_extract_string(payload_json, '$.window.next_offset') AS INTEGER) AS window_next_offset,
            json_extract(payload_json, '$.metadata') AS metadata_json,
            json_extract(payload_json, '$.links') AS links_json,
            json_extract(payload_json, '$.summary') AS summary_json,
            payload_json
        FROM {source}
        WHERE event_name IN ('tool.get_content.response', 'tool.batch_get_content.response')
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_answer_events AS
        SELECT
            event_id,
            recorded_at,
            coalesce(run_key, trace_id, event_id) AS run_key,
            event_name,
            tool_name,
            query,
            research_goal,
            model,
            json_extract_string(payload_json, '$.answer') AS answer,
            json_extract(payload_json, '$.sources') AS sources_json,
            json_extract(payload_json, '$.grounding_chunks') AS grounding_chunks_json,
            json_extract(payload_json, '$.structured_result') AS structured_result_json,
            json_extract(payload_json, '$.citations') AS citations_json,
            payload_json
        FROM {source}
        WHERE event_name IN (
            'tool.gemini_search.response',
            'tool.perplexity_search.response',
            'tool.quick_web_search.response'
        )
        """,
        build_candidate_survival_view_sql(target),
    ]

