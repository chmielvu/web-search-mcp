"""DuckDB/MotherDuck analytics views built directly from raw event rows."""

from __future__ import annotations


def build_raw_view_sql(target: str, *, source_table: str = "analytics_event_raw") -> list[str]:
    source = f"{target}.{source_table}"
    return [
        f"""
        CREATE OR REPLACE VIEW {target}.vw_events AS
        SELECT
            event_id,
            recorded_at,
            coalesce(run_key, trace_id, event_id) AS run_key,
            event_name,
            coalesce(tool_name, json_extract_string(payload_json, '$.tool_name')) AS tool_name,
            phase,
            query,
            normalized_query,
            research_goal,
            coalesce(
                provider,
                json_extract_string(payload_json, '$.provider'),
                json_extract_string(payload_json, '$.provider_name')
            ) AS provider,
            model,
            duration_ms,
            input_count,
            output_count,
            trace_id,
            span_id,
            cache_hit,
            payload_json
        FROM {source}
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_run_timeline AS
        SELECT
            run_key,
            MIN(recorded_at) AS first_seen_at,
            MAX(recorded_at) AS last_seen_at,
            COUNT(*) AS event_count,
            COUNT(*) FILTER (WHERE event_name LIKE 'query.rewrite.%') AS rewrite_events,
            COUNT(*) FILTER (WHERE event_name LIKE 'rerank.%') AS rerank_events,
            COUNT(*) FILTER (WHERE event_name LIKE 'tool.get_content.%'
                               OR event_name LIKE 'tool.batch_get_content.%') AS fetch_events,
            COUNT(*) FILTER (WHERE event_name IN (
                'tool.gemini_search.response',
                'tool.grok_search.response',
                'tool.quick_web_search.response'
            )) AS answer_events
        FROM {target}.vw_events
        GROUP BY run_key
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_provider_results AS
        WITH provider_events AS (
            SELECT
                event_id,
                recorded_at,
                coalesce(run_key, trace_id, event_id) AS run_key,
                event_name,
                coalesce(
                    provider,
                    json_extract_string(payload_json, '$.provider'),
                    json_extract_string(payload_json, '$.provider_name')
                ) AS provider_name,
                query,
                normalized_query,
                research_goal,
                duration_ms,
                payload_json
            FROM {source}
            WHERE event_name = 'provider.search.result'
        )
        SELECT
            e.event_id,
            e.recorded_at,
            e.run_key,
            e.event_name,
            e.provider_name,
            e.provider_name AS provider,
            e.query,
            e.normalized_query,
            e.research_goal,
            e.duration_ms,
            CAST(r.key AS INTEGER) AS result_index,
            coalesce(
                json_extract_string(r.value, '$.link'),
                json_extract_string(r.value, '$.url')
            ) AS url,
            json_extract_string(r.value, '$.title') AS title,
            json_extract_string(r.value, '$.snippet') AS snippet,
            coalesce(
                json_extract_string(r.value, '$.domain'),
                regexp_extract(
                    coalesce(
                        json_extract_string(r.value, '$.link'),
                        json_extract_string(r.value, '$.url')
                    ),
                    'https?://(?:www\\.)?([^/]+)',
                    1
                )
            ) AS domain,
            json_extract(r.value, '$.providers') AS providers_json,
            CAST(json_extract_string(r.value, '$.provider_count') AS INTEGER) AS provider_count,
            CAST(json_extract_string(r.value, '$.score') AS DOUBLE) AS score,
            CAST(json_extract_string(r.value, '$.raw_score') AS DOUBLE) AS raw_score,
            json_extract(r.value, '$.providers') AS source_engines_json,
            json_extract_string(r.value, '$.category') AS category,
            json_extract_string(r.value, '$.published_date') AS published_date,
            NULL AS branch_index,
            NULL AS branch_query,
            NULL AS branch_weight,
            e.payload_json
        FROM provider_events e,
             json_each(json_extract(e.payload_json, '$.results')) AS r
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_branch_candidates AS
        SELECT
            e.event_id,
            e.recorded_at,
            coalesce(e.run_key, e.trace_id, e.event_id) AS run_key,
            e.event_name,
            NULL AS provider_name,
            NULL AS provider,
            e.query,
            coalesce(
                json_extract_string(r.value, '$.link'),
                json_extract_string(r.value, '$.url')
            ) AS url,
            json_extract_string(r.value, '$.title') AS title,
            json_extract_string(r.value, '$.snippet') AS snippet,
            coalesce(
                json_extract_string(r.value, '$.domain'),
                regexp_extract(
                    coalesce(
                        json_extract_string(r.value, '$.link'),
                        json_extract_string(r.value, '$.url')
                    ),
                    'https?://(?:www\\.)?([^/]+)',
                    1
                )
            ) AS domain,
            json_extract(r.value, '$.providers') AS providers_json,
            CAST(json_extract_string(r.value, '$.provider_count') AS INTEGER) AS provider_count,
            CAST(json_extract_string(r.value, '$.score') AS DOUBLE) AS score,
            CAST(json_extract_string(r.value, '$.raw_score') AS DOUBLE) AS raw_score,
            json_extract(b.value, '$.providers') AS source_engines_json,
            json_extract_string(r.value, '$.category') AS category,
            json_extract_string(r.value, '$.published_date') AS published_date,
            CAST(b.key AS INTEGER) AS branch_index,
            json_extract_string(b.value, '$.query') AS branch_query,
            CAST(json_extract_string(b.value, '$.weight') AS DOUBLE) AS branch_weight,
            CAST(r.key AS INTEGER) AS result_index,
            json_extract(b.value, '$.providers') AS result_providers_json,
            e.payload_json AS payload_json
        FROM {source} AS e,
             json_each(json_extract(e.payload_json, '$.branches')) AS b,
             json_each(json_extract(b.value, '$.results')) AS r
        WHERE e.event_name = 'search.pipeline.branches'
        """,
    ]
