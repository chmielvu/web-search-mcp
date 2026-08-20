from __future__ import annotations

import duckdb
import pytest

from kindly_web_search_mcp_server.analytics.duckdb_store import (
    ensure_store_schema,
    insert_result_label,
    insert_result_labels,
)
from kindly_web_search_mcp_server.analytics.quality_metrics import (
    compute_discounted_cumulative_gain,
    compute_positional_discount,
    replay_result_labels_aggregate,
)


def test_positional_discount_uses_zero_based_position() -> None:
    assert compute_positional_discount(4.0, 0) == pytest.approx(4.0)
    assert compute_positional_discount(4.0, 1) == pytest.approx(4.0 / 1.5849625)
    assert compute_discounted_cumulative_gain([4.0, 2.0]) == pytest.approx(
        4.0 + 2.0 / 1.5849625
    )
    with pytest.raises(ValueError):
        compute_positional_discount(1.0, -1)


def test_result_label_writer_and_replay(tmp_path) -> None:
    db_path = tmp_path / "labels.duckdb"
    ensure_store_schema(db_path=str(db_path))

    insert_result_label(
        db_path=str(db_path),
        sync=True,
        run_key="run-1",
        position=0,
        label=4,
        raw_url="https://example.com/a",
        source="human",
        annotator_id="reviewer-1",
    )
    insert_result_labels(
        [
            {
                "run_key": "run-1",
                "position": 1,
                "label": 2,
                "raw_url": "https://example.com/b",
                "source": "llm_judge",
                "annotator_id": "judge-model",
            },
            {
                "run_key": "run-2",
                "position": 0,
                "label": 1,
                "raw_url": "https://example.com/c",
                "source": "eval",
            },
        ],
        db_path=str(db_path),
        sync=True,
    )

    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = connection.execute(
            "SELECT run_key, position, stage, label, source, discounted_gain FROM result_labels "
            "ORDER BY run_key, position"
        ).fetchall()
    finally:
        connection.close()

    assert rows[0][0:5] == ("run-1", 0, "final", 4.0, "human")
    assert rows[0][5] == pytest.approx(4.0)
    assert rows[1][5] == pytest.approx(2.0 / 1.5849625)
    assert replay_result_labels_aggregate(run_key="run-1", db_path=str(db_path)) == [
        {
            "run_key": "run-1",
            "stage": "final",
            "source": "human",
            "rubric_version": "v1",
            "label_count": 1,
            "discounted_gain": 4.0,
        },
        {
            "run_key": "run-1",
            "stage": "final",
            "source": "llm_judge",
            "rubric_version": "v1",
            "label_count": 1,
            "discounted_gain": pytest.approx(2.0 / 1.5849625),
        },
    ]


def test_result_label_async_dispatch(tmp_path) -> None:
    from kindly_web_search_mcp_server.analytics.async_writes import drain_duckdb_writes

    db_path = tmp_path / "labels_async.duckdb"
    ensure_store_schema(db_path=str(db_path))
    insert_result_label(
        db_path=str(db_path),
        run_key="run-async",
        position=0,
        label=1,
        raw_url="https://example.com/async",
        source="eval",
    )
    drain_duckdb_writes()
    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        row = connection.execute(
            "SELECT COUNT(*) FROM result_labels WHERE run_key = 'run-async'"
        ).fetchone()
        assert row is not None
        assert row[0] == 1
    finally:
        connection.close()
