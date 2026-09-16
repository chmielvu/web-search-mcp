"""Batched analytics writes for rerank candidate survival rows."""

from __future__ import annotations

import json
from typing import Any

import duckdb

from ..settings import settings
from .writers import _LOCK, _RC_TABLE_NAME, _db_path, _ensure_rerank_candidates

_RERANK_CANDIDATE_COLUMNS = [
    "run_key",
    "stage",
    "link",
    "candidate_id",
    "canonical_result_id",
    "rank_before",
    "rank_after",
    "final_score_before",
    "final_score_after",
    "bm25_score",
    "bm25_rank",
    "bi_encoder_score",
    "bi_encoder_rank",
    "cross_encoder_score",
    "rankllm_score",
    "retrieval_rrf_score",
    "recency_score",
    "diversity_penalty",
    "survived",
    "diversity_removed",
    "payload_json",
]


def insert_rerank_candidate_rows_batch(
    rows: list[dict[str, Any]],
    *,
    db_path: str | None = None,
) -> None:
    """Insert one rerank stage's candidate rows with one DuckDB connection."""
    if not rows or not settings.analytics_enabled:
        return

    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    placeholders = ", ".join("?" for _ in _RERANK_CANDIDATE_COLUMNS)
    col_list = ", ".join(_RERANK_CANDIDATE_COLUMNS)
    values = []
    for row in rows:
        normalized = dict(row)
        normalized.setdefault(
            "survived",
            row.get("rank_after") is not None,
        )
        normalized["diversity_removed"] = bool(row.get("diversity_removed", False))
        values.append(
            [
                (
                    normalized.get(column)
                    if column != "payload_json"
                    else json.dumps(normalized.get(column) or {})
                )
                for column in _RERANK_CANDIDATE_COLUMNS
            ]
        )

    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_rerank_candidates(connection)
            connection.executemany(
                f"""
                INSERT INTO {_RC_TABLE_NAME} ({col_list})
                VALUES ({placeholders})
                """,
                values,
            )
        finally:
            connection.close()
