"""Observability store — stripped to provider health + ID helpers in clean cutover.

Legacy insert functions and view builders targeting dropped tables have been
removed. The unified 9-table schema in ``writers/`` (re-exported via
``duckdb_store``) now covers search_runs, search_branches, provider_calls,
search_candidates, rerank_stages, rerank_candidates, final_results,
query_embeddings, and candidate_embeddings.
"""

from __future__ import annotations

from .observability_ids import _candidate_id, _canonical_result_id, _field
from .observability_tables import (
    ensure_pipeline_observability_tables,
    insert_provider_health_transition,
)

__all__ = [
    "_candidate_id",
    "_canonical_result_id",
    "_field",
    "ensure_pipeline_observability_tables",
    "insert_provider_health_transition",
]
