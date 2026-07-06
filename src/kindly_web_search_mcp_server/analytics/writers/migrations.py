"""Legacy backfill migrations for the ``search_events`` table.

These UPDATE statements were originally inlined in ``_ensure_schema`` and
remain here so a fresh DuckDB file with the legacy event shape is upgraded
to the current column set on first connect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .table_names import _TABLE_NAME

if TYPE_CHECKING:
    import duckdb


def apply_search_events_migrations(connection: "duckdb.DuckDBPyConnection") -> None:
    """Backfill null columns in ``search_events`` from ``payload_json``."""
    connection.execute(
        f"UPDATE {_TABLE_NAME} SET event_id = uuid()::VARCHAR WHERE event_id IS NULL"
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET run_key = coalesce(trace_id, json_extract_string(payload_json, '$.request_fingerprint'))
        WHERE run_key IS NULL
        """
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET tool_name = json_extract_string(payload_json, '$.tool_name')
        WHERE tool_name IS NULL
        """
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET phase = regexp_extract(event_name, '[^.]+$', 0)
        WHERE phase IS NULL
        """
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET provider = coalesce(
            provider,
            json_extract_string(payload_json, '$.provider'),
            json_extract_string(payload_json, '$.provider_name')
        )
        WHERE provider IS NULL
        """
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET model_used = coalesce(
            model_used,
            model,
            json_extract_string(payload_json, '$.model_used'),
            json_extract_string(payload_json, '$.model')
        )
        WHERE model_used IS NULL
        """
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET input_count = coalesce(
            input_count,
            CAST(json_extract_string(payload_json, '$.input_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.input_result_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.input_list_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.num_results_requested') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.num_results') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.tool_calls_count') AS INTEGER)
        )
        WHERE input_count IS NULL
        """
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET output_count = coalesce(
            output_count,
            CAST(json_extract_string(payload_json, '$.output_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.result_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.merged_result_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.final_result_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.output_result_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.total_returned') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.success_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.sources_count') AS INTEGER)
        )
        WHERE output_count IS NULL
        """
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET input_tokens = coalesce(
            input_tokens,
            CAST(json_extract_string(payload_json, '$.input_tokens') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.usage.input_tokens') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.usage.prompt_tokens') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.usage.prompt_token_count') AS INTEGER)
        )
        WHERE input_tokens IS NULL
        """
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET output_tokens = coalesce(
            output_tokens,
            CAST(json_extract_string(payload_json, '$.output_tokens') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.usage.output_tokens') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.usage.completion_tokens') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.usage.response_token_count') AS INTEGER)
        )
        WHERE output_tokens IS NULL
        """
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET duration_ms = coalesce(
            duration_ms,
            CAST(json_extract_string(payload_json, '$.duration_ms') AS DOUBLE),
            CAST(json_extract_string(payload_json, '$.duration_seconds') AS DOUBLE) * 1000.0
        )
        WHERE duration_ms IS NULL
        """
    )


__all__ = ["apply_search_events_migrations"]
