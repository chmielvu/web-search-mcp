"""Tests for SQLite graph artifact persistence."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import sqlite3
import pytest

from kindly_web_search_mcp_server.analytics.graph_store import (
    GraphSnapshot,
    _CACHE_LOCK,
    _CACHED_INDICES,
    load_latest_graph_index,
    publish_graph_snapshot,
)


def _snapshot(generation_id: str, built_at: datetime) -> GraphSnapshot:
    fingerprint = f"fingerprint-{generation_id}"
    return GraphSnapshot(
        generation_id=generation_id,
        built_at=built_at,
        source_cutoff=built_at,
        label_version="v1",
        source_fingerprint=fingerprint,
        config={
            "scoring_policy_version": "judge_gain_confidence_mean_v1",
            "source_fingerprint": fingerprint,
        },
        query_node_count=2,
        document_node_count=2,
        edge_count=2,
        shared_document_count=1,
        neighbors=(
            {
                "query_norm": "source query",
                "related_norm": "related query",
                "rank": 1,
                "score": 1.0,
                "method": "adamic_adar",
                "support_count": 2,
            },
        ),
        result_features=(
            {
                "canonical_result_id": "document-1",
                "birank_score": 0.5,
                "pagerank_score": 0.5,
                "weighted_degree": 1.0,
            },
        ),
    )


def _clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHED_INDICES.clear()


def test_publish_uses_sqlite_wal_and_loads_ready_generation(tmp_path) -> None:
    sqlite_path = tmp_path / "graph.sqlite"
    now = datetime.now(timezone.utc)

    publish_graph_snapshot(_snapshot("generation-1", now), sqlite_path=str(sqlite_path))
    _clear_cache()

    connection = sqlite3.connect(str(sqlite_path))
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT status FROM graph_generations").fetchone()[0] == "ready"
    finally:
        connection.close()


def test_failed_publication_rolls_back_without_replacing_previous_generation(tmp_path) -> None:
    sqlite_path = tmp_path / "graph.sqlite"
    now = datetime.now(timezone.utc)
    publish_graph_snapshot(_snapshot("generation-1", now), sqlite_path=str(sqlite_path))

    broken = replace(_snapshot("generation-2", now), neighbors=({"query_norm": "missing fields"},))
    with pytest.raises((KeyError, TypeError, ValueError)):
        publish_graph_snapshot(broken, sqlite_path=str(sqlite_path))

    _clear_cache()
    index = load_latest_graph_index(sqlite_path=str(sqlite_path), max_age_seconds=3600)
    assert index is not None
    assert index.generation_id == "generation-1"

    connection = sqlite3.connect(str(sqlite_path))
    try:
        rows = connection.execute(
            "SELECT generation_id, status FROM graph_generations ORDER BY generation_id"
        ).fetchall()
    finally:
        connection.close()
    assert rows == [("generation-1", "ready")]


def test_sqlite_artifacts_are_path_isolated(tmp_path) -> None:
    now = datetime.now(timezone.utc)
    first_path = tmp_path / "first.sqlite"
    second_path = tmp_path / "second.sqlite"
    publish_graph_snapshot(_snapshot("first", now), sqlite_path=str(first_path))
    publish_graph_snapshot(_snapshot("second", now), sqlite_path=str(second_path))
    _clear_cache()

    first = load_latest_graph_index(sqlite_path=str(first_path), max_age_seconds=3600)
    second = load_latest_graph_index(sqlite_path=str(second_path), max_age_seconds=3600)
    assert first is not None and second is not None
    assert first.generation_id == "first"
    assert second.generation_id == "second"
