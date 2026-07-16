from __future__ import annotations

import duckdb

from kindly_web_search_mcp_server.analytics.rerank_candidate_writes import (
    insert_rerank_candidate_rows_batch,
)


def test_insert_rerank_candidate_rows_batch_persists_stage_rows(tmp_path) -> None:
    db_path = tmp_path / "rerank_candidates.duckdb"

    insert_rerank_candidate_rows_batch(
        [
            {
                "run_key": "run-batch",
                "stage": "cohere_fast",
                "link": "https://example.com/a",
                "rank_before": 2,
                "rank_after": 1,
                "score_before": 0.2,
                "score_after": 0.9,
                "score_after_relevance": 0.9,
                "score_after_recency": None,
                "score_after_entity": None,
                "recency_boost": None,
                "entity_overlap_score": None,
                "survived": True,
                "payload_json": {"candidate_id": "a"},
            },
            {
                "run_key": "run-batch",
                "stage": "cohere_fast",
                "link": "https://example.com/b",
                "rank_before": 1,
                "rank_after": None,
                "score_before": 0.7,
                "score_after": None,
                "score_after_relevance": None,
                "score_after_recency": None,
                "score_after_entity": None,
                "recency_boost": None,
                "entity_overlap_score": None,
                "survived": False,
                "payload_json": {"candidate_id": "b"},
            },
        ],
        db_path=str(db_path),
    )

    with duckdb.connect(str(db_path), read_only=True) as con:
        rows = con.execute(
            """
            SELECT link, rank_before, rank_after, score_after, survived
            FROM rerank_candidates
            ORDER BY link
            """
        ).fetchall()

    assert rows == [
        ("https://example.com/a", 2, 1, 0.9, True),
        ("https://example.com/b", 1, None, None, False),
    ]
