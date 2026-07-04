from __future__ import annotations

from pathlib import Path


def test_eval_schema_includes_existing_and_phase_1_2_tables() -> None:
    import duckdb

    from kindly_web_search_mcp_server.analytics.evals import ensure_eval_tables

    db_path = Path("test_eval_schema.duckdb")
    if db_path.exists():
        db_path.unlink()
    duckdb.connect(str(db_path)).close()

    ensure_eval_tables(db_path=str(db_path))

    con = duckdb.connect(str(db_path), read_only=True)
    tables = {
        row[0]
        for row in con.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main'
            """
        ).fetchall()
    }

    expected_tables = {
        "analytics_sync_state",
        "eval_candidate_sets",
        "eval_cases",
        "eval_failures",
        "eval_judge_calls",
        "eval_observations",
        "eval_runs",
        "eval_scores",
        "eval_tool_calls",
        "llm_quality_scores",
    }
    assert expected_tables.issubset(tables)

    required_columns = {
        "eval_tool_calls": {"tool_call_id", "tool_name"},
        "eval_candidate_sets": {"candidate_set_id", "set_name", "candidates_json"},
        "eval_scores": {"score_id", "metric_name", "score_value"},
        "eval_judge_calls": {"judge_call_id", "judge_model", "score_value"},
        "eval_failures": {"failure_id", "failure_code"},
    }
    common_columns = {
        "eval_run_id",
        "eval_case_id",
        "recorded_at",
        "run_key",
        "payload_json",
    }

    for table, columns in required_columns.items():
        table_columns = {
            row[1] for row in con.execute(f"PRAGMA table_info('{table}')").fetchall()
        }
        assert common_columns.issubset(table_columns)
        assert columns.issubset(table_columns)

    con.close()
    if db_path.exists():
        db_path.unlink()


def test_eval_case_models_validate_minimum_contract() -> None:
    from kindly_web_search_mcp_server.evals.cases import (
        CandidateSet,
        EvalCase,
        ExpectedToolCall,
    )

    case = EvalCase(
        eval_case_id="case-1",
        suite_name="tool-routing",
        query="https://example.com/docs",
        research_goal="fetch a known URL",
        expected_tool_calls=[
            ExpectedToolCall(tool_name="get_content", required=True),
            ExpectedToolCall(tool_name="web_search", required=False, forbidden=True),
        ],
        candidate_sets=[
            CandidateSet(
                name="post_rerank",
                candidates=[
                    {"url": "https://example.com/docs", "domain": "example.com"}
                ],
            )
        ],
    )

    assert case.eval_case_id == "case-1"
    assert case.expected_tool_calls[0].tool_name == "get_content"
    assert case.candidate_sets[0].candidates[0]["domain"] == "example.com"


def test_deterministic_eval_metrics() -> None:
    from kindly_web_search_mcp_server.evals.metrics import (
        expected_tool_called,
        forbidden_tool_not_called,
        latency_within_budget,
        mrr_at_k,
        ndcg_at_k,
        top_k_domain_hit,
    )

    tool_calls = [
        {"tool_name": "web_search"},
        {"tool_name": "get_content"},
    ]
    ranked_candidates = [
        {"url": "https://noise.example/a", "relevance": 0},
        {"url": "https://docs.python.org/3/library/math.html", "relevance": 3},
        {"url": "https://docs.python.org/3/library/json.html", "relevance": 2},
    ]

    assert expected_tool_called(tool_calls, "get_content") == 1.0
    assert expected_tool_called(tool_calls, "gemini_search") == 0.0
    assert forbidden_tool_not_called(tool_calls, "grok_search") == 1.0
    assert forbidden_tool_not_called(tool_calls, "web_search") == 0.0
    assert latency_within_budget(250, 500) == 1.0
    assert latency_within_budget(750, 500) == 0.0
    assert mrr_at_k(ranked_candidates, "docs.python.org", 3) == 0.5
    assert round(ndcg_at_k(ranked_candidates, 3), 3) == 0.693
    assert top_k_domain_hit(ranked_candidates, "docs.python.org", 2) == 1.0
    assert top_k_domain_hit(ranked_candidates, "pypi.org", 2) == 0.0
