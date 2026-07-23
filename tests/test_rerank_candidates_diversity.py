"""BUG4: rerank_candidates diversity_removed column parity."""

from __future__ import annotations

import tempfile
from pathlib import Path

import duckdb
import pytest

from kindly_web_search_mcp_server.analytics.rerank_candidate_writes import (
    insert_rerank_candidate_rows_batch,
)
from kindly_web_search_mcp_server.analytics.writers.schema import ensure_store_schema
from kindly_web_search_mcp_server import settings as settings_module


@pytest.fixture
def temp_db(monkeypatch):
    tmp_dir = tempfile.mkdtemp(prefix="rerank_div_")
    db_path = str(Path(tmp_dir) / "rerank.duckdb")
    monkeypatch.setattr(settings_module.settings, "analytics_duckdb_path", db_path)
    monkeypatch.setattr(settings_module.settings, "analytics_enabled", True)
    monkeypatch.setattr(settings_module.settings, "flockmtl_enabled", False)
    ensure_store_schema(db_path=db_path)
    yield db_path
    for leftover in Path(tmp_dir).glob("*"):
        leftover.unlink(missing_ok=True)
    Path(tmp_dir).rmdir()


def test_diversity_removed_roundtrip(temp_db: str) -> None:
    insert_rerank_candidate_rows_batch(
        [
            {
                "run_key": "rk-1",
                "stage": "diversity",
                "link": "https://example.com/a",
                "rank_before": 1,
                "rank_after": None,
                "score_before": 0.9,
                "score_after": None,
                "diversity_removed": True,
            },
            {
                "run_key": "rk-1",
                "stage": "diversity",
                "link": "https://example.com/b",
                "rank_before": 2,
                "rank_after": 1,
                "score_before": 0.8,
                "score_after": 0.8,
                "diversity_removed": False,
            },
        ],
        db_path=temp_db,
    )

    con = duckdb.connect(temp_db, read_only=True)
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info('rerank_candidates')").fetchall()}
        assert "diversity_removed" in cols
        assert "survived" in cols

        rows = con.execute(
            "SELECT link, diversity_removed, survived "
            "FROM rerank_candidates WHERE run_key = 'rk-1' ORDER BY link"
        ).fetchall()
        assert rows == [
            ("https://example.com/a", True, False),
            ("https://example.com/b", False, True),
        ]
    finally:
        con.close()


def test_ensure_adds_diversity_removed_to_legacy_table(monkeypatch) -> None:
    """Existing DBs without the column gain it via _ensure_columns."""
    tmp_dir = tempfile.mkdtemp(prefix="rerank_legacy_")
    legacy_path = str(Path(tmp_dir) / "legacy.duckdb")
    monkeypatch.setattr(settings_module.settings, "analytics_duckdb_path", legacy_path)
    monkeypatch.setattr(settings_module.settings, "flockmtl_enabled", False)
    con = duckdb.connect(legacy_path)
    try:
        con.execute(
            """
            CREATE TABLE rerank_candidates (
                recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                run_key VARCHAR NOT NULL,
                stage VARCHAR NOT NULL,
                link VARCHAR NOT NULL,
                survived BOOLEAN NOT NULL,
                payload_json JSON
            )
            """
        )
    finally:
        con.close()

    ensure_store_schema(db_path=legacy_path)
    con = duckdb.connect(legacy_path, read_only=True)
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info('rerank_candidates')").fetchall()}
        assert "diversity_removed" in cols
    finally:
        con.close()
    for leftover in Path(tmp_dir).glob("*"):
        leftover.unlink(missing_ok=True)
    Path(tmp_dir).rmdir()
