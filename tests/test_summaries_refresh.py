"""Tests for refresh_summary_tables."""

from __future__ import annotations

from pathlib import Path

import duckdb

from kindly_web_search_mcp_server.analytics.duckdb_store import (
    _SUM_ID_TABLE_NAME,
    _SUM_PVD_TABLE_NAME,
    _SUM_QD_TABLE_NAME,
    _SUM_RD_TABLE_NAME,
    _ensure_provider_calls,
    _ensure_query_rewrites,
    _ensure_query_understanding,
    _ensure_rerank_stages,
    _ensure_search_quality_scores,
)
from kindly_web_search_mcp_server.analytics.summaries import refresh_summary_tables


class TestRefreshSummaryTables:
    """Verify that refresh_summary_tables correctly aggregates from raw tables."""

    def test_refresh_all_tables(self) -> None:
        db_path = Path("test_summaries_refresh.duckdb")
        if db_path.exists():
            db_path.unlink()

        try:
            # ── Seed raw tables ────────────────────────────────────────
            con = duckdb.connect(str(db_path))

            _ensure_provider_calls(con)
            _ensure_query_understanding(con)
            _ensure_query_rewrites(con)
            _ensure_rerank_stages(con)
            _ensure_search_quality_scores(con)

            # provider_calls rows
            con.execute(
                """
                INSERT INTO provider_calls (run_key, recorded_at, provider, num_results_returned, duration_ms, error_code)
                VALUES
                    ('r1', now(), 'searxng', 10, 100.0, NULL),
                    ('r2', now(), 'searxng',  8, 150.0, NULL),
                    ('r3', now(), 'brave',   12, 200.0, 'RATE_LIMIT'),
                    ('r4', now() - INTERVAL '1 day', 'searxng', 10, 110.0, NULL),
                    ('r5', now() - INTERVAL '3 days', 'searxng',  9, 120.0, NULL)
                """
            )

            # query_understanding rows
            con.execute(
                """
                INSERT INTO query_understanding
                    (run_key, recorded_at, intent, confidence, should_decompose, fallback_used)
                VALUES
                    ('r1', now(), 'research', 0.95, true,  false),
                    ('r2', now(), 'navigational', 0.80, false, true),
                    ('r3', now(),  NULL,      NULL,  false, false),
                    ('r4', now() - INTERVAL '1 day', 'research', 0.90, true, false)
                """
            )

            # query_rewrites rows (for avg_rewrite_variants)
            con.execute(
                """
                INSERT INTO query_rewrites (run_key, recorded_at, variant_index, query)
                VALUES
                    ('r1', now(), 0, 'research query v0'),
                    ('r1', now(), 1, 'research query v1'),
                    ('r2', now(), 0, 'nav query v0'),
                    ('r4', now() - INTERVAL '1 day', 0, 'research day old v0'),
                    ('r4', now() - INTERVAL '1 day', 1, 'research day old v1')
                """
            )

            # rerank_stages rows
            con.execute(
                """
                INSERT INTO rerank_stages
                    (run_key, recorded_at, stage, provider, input_count, output_count, duration_ms, max_score, entity_overlap_enabled)
                VALUES
                    ('r1', now(), 'bi_encoder', 'voyage', 100, 50,  50.0, 0.85, true),
                    ('r1', now(), 'cross_encoder', 'voyage', 50, 10, 80.0, 0.92, false),
                    ('r2', now(), 'bi_encoder', 'jina',   80, 40, 60.0, 0.78, false),
                    ('r4', now() - INTERVAL '1 day', 'bi_encoder', 'voyage', 90, 45, 55.0, 0.88, true)
                """
            )

            # search_quality_scores rows
            con.execute(
                """
                INSERT INTO search_quality_scores
                    (run_key, recorded_at, provider_overlap_rate, domain_diversity_count,
                     domain_diversity_ratio, rerank_compression_ratio, top_score)
                VALUES
                    ('r1', now(), 0.3, 5, 0.6, 0.5, 0.95),
                    ('r2', now(), 0.4, 3, 0.5, 0.4, 0.90),
                    ('r4', now() - INTERVAL '1 day', 0.2, 6, 0.7, 0.45, 0.92)
                """
            )

            con.close()

            # ── Refresh ────────────────────────────────────────────────
            refresh_summary_tables(db_path=str(db_path))

            # ── Assert summary_provider_daily ───────────────────────────
            con = duckdb.connect(str(db_path))

            rows = con.execute(
                f"SELECT day, provider, query_count, avg_results_returned, "
                f"p50_results_returned, avg_latency_ms, p50_latency_ms, "
                f"p95_latency_ms, error_rate, distinct_queries "
                f"FROM {_SUM_PVD_TABLE_NAME} ORDER BY provider, day"
            ).fetchall()
            # Expect 3 rows: brave (today, 1 row r3),
            # searxng (yesterday, 1 row r4), searxng (today, 2 rows r1+r2).
            # r5 (3 days ago) excluded. Ordered by provider alpha, then day ASC
            # (yesterday date < today date).
            assert len(rows) == 3, f"Expected 3 summary rows, got {len(rows)}: {rows}"

            # brave, today: count=1, error_rate=1.0 (1 error / 1 total)
            row = rows[0]
            assert row[1] == "brave"
            assert row[2] == 1
            assert row[8] == 1.0  # error_rate

            # searxng, yesterday: count=1
            row = rows[1]
            assert row[1] == "searxng"
            assert row[2] == 1

            # searxng, today: count=2, avg_results=(10+8)/2=9.0, avg_latency=(100+150)/2=125
            row = rows[2]
            assert row[1] == "searxng"
            assert row[2] == 2  # query_count
            assert row[3] == 9.0  # avg_results_returned
            assert row[5] == 125.0  # avg_latency_ms

            # ── Assert summary_intent_daily ────────────────────────────
            rows = con.execute(
                f"SELECT day, intent, query_count, avg_confidence, "
                f"decomposition_rate, fallback_rate, avg_rewrite_variants "
                f"FROM {_SUM_ID_TABLE_NAME} ORDER BY day DESC, intent"
            ).fetchall()
            # navigational (today), research (today), research (yesterday)
            assert len(rows) == 3, f"Expected 3 summary rows, got {len(rows)}: {rows}"

            # navigational, today: count=1 (r2), confidence=0.80, decomposition=0.0, fallback=1.0, avg_rewrite_variants=1.0
            row = rows[0]
            assert row[1] == "navigational"
            assert row[2] == 1
            assert row[3] == 0.80
            assert row[4] == 0.0  # decomposition_rate
            assert row[5] == 1.0  # fallback_rate
            assert row[6] == 1.0  # avg_rewrite_variants

            # research, today: count=1 (r1), confidence=0.95, decomposition=1.0, fallback=0.0, avg_rewrite_variants=2.0
            row = rows[1]
            assert row[1] == "research"
            assert row[2] == 1
            assert row[3] == 0.95
            assert row[4] == 1.0
            assert row[5] == 0.0
            assert row[6] == 2.0

            # research, yesterday: count=1 (r4), confidence=0.90, decomposition=1.0, avg_rewrite_variants=2.0
            row = rows[2]
            assert row[1] == "research"
            assert row[2] == 1
            assert row[3] == 0.90
            assert row[4] == 1.0
            assert row[5] == 0.0
            assert row[6] == 2.0

            # ── Assert summary_rerank_daily ────────────────────────────
            rows = con.execute(
                f"SELECT day, stage, provider, runs_count, avg_compression_ratio, "
                f"avg_max_score, p50_latency_ms, p95_latency_ms, entity_overlap_runs "
                f"FROM {_SUM_RD_TABLE_NAME} ORDER BY day DESC, stage, provider"
            ).fetchall()
            # 4 rows: bi_encoder/jina(today), bi_encoder/voyage(today),
            # cross_encoder/voyage(today), bi_encoder/voyage(yesterday)
            assert len(rows) == 4, f"Expected 4 summary rows, got {len(rows)}: {rows}"

            # bi_encoder/jina, today: runs=1, compression=80/40=2.0, max_score=0.78, overlap=0
            row = rows[0]
            assert row[1] == "bi_encoder"
            assert row[2] == "jina"
            assert row[3] == 1  # runs_count
            assert row[4] == 2.0  # avg_compression_ratio
            assert row[5] == 0.78  # avg_max_score
            assert row[8] == 0  # entity_overlap_runs

            # bi_encoder/voyage, today: runs=1, compression=100/50=2.0, max_score=0.85, overlap=1
            row = rows[1]
            assert row[1] == "bi_encoder"
            assert row[2] == "voyage"
            assert row[3] == 1  # runs_count
            assert row[4] == 2.0  # avg_compression_ratio
            assert row[5] == 0.85  # avg_max_score
            assert row[8] == 1  # entity_overlap_runs

            # ── Assert summary_quality_daily ───────────────────────────
            rows = con.execute(
                f"SELECT day, avg_overlap_rate, avg_domain_diversity, "
                f"avg_domain_diversity_ratio, avg_compression_ratio, avg_top_score "
                f"FROM {_SUM_QD_TABLE_NAME} ORDER BY day DESC"
            ).fetchall()
            # 2 rows: today (r1+r2) and yesterday (r4)
            assert len(rows) == 2, f"Expected 2 summary rows, got {len(rows)}: {rows}"

            # today: avg_overlap_rate=(0.3+0.4)/2=0.35, avg_domain_diversity=(5+3)/2=4.0,
            # avg_domain_diversity_ratio=(0.6+0.5)/2=0.55, avg_compression_ratio=(0.5+0.4)/2=0.45,
            # avg_top_score=(0.95+0.90)/2=0.925
            row = rows[0]
            assert row[1] == 0.35  # avg_overlap_rate
            assert row[2] == 4.0  # avg_domain_diversity
            assert row[3] == 0.55  # avg_domain_diversity_ratio
            assert row[4] == 0.45  # avg_compression_ratio
            assert row[5] == 0.925  # avg_top_score

            con.close()

        finally:
            if db_path.exists():
                db_path.unlink()
