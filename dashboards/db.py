"""Search quality dashboard — DuckDB queries with simulated data seeder.

Pattern: duckdb.connect(read_only=True) → execute → .df() → close.
Proven in production by olist-e-commerce-analytics and weather-pipeline.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

DB_PATH = Path(__file__).resolve().parent.parent / ".kindly" / "analytics" / "search_events.duckdb"

TEAL = "#2a9d8f"
ORANGE = "#e76f51"
GRAY = "#6b7280"

# ---------------------------------------------------------------------------
# Simulated data — realistic search calls with provider results, eval scores,
# rewrite variants, and failure cases.  Used to seed DuckDB on first run.
# ---------------------------------------------------------------------------

_SEED_RUN_KEYS = {
    "python_web": "run_py_web_framework_2026",
    "fastapi_bug": "run_fastapi_middleware_error",
    "rust_vs_go": "run_rust_go_microservices",
}

_SEED_CALLS = [
    {
        "call_id": "call_a1b2c3",
        "run_key": _SEED_RUN_KEYS["python_web"],
        "trace_id": "trace_789",
        "query": "best python web framework 2026",
        "normalized_query": "python web framework comparison 2026",
        "research_goal": "Compare FastAPI, Django, Flask for a new production project",
        "tool_name": "web_search",
        "tool_profile": "research",
        "providers_requested": ["searxng", "tavily", "brave"],
        "num_results": 10,
        "final_count": 8,
        "duration_ms": 2340,
        "status": "ok",
        "passed": True,
        "pass_rate_score": 0.82,
        "faithfulness": 0.78,
        "groundedness": 0.85,
        "context_precision": 0.72,
        "context_recall": 0.79,
        "answer_relevance": 0.88,
        "mrr_at_10": 0.92,
        "ndcg_at_10": 0.81,
        "failure_categories": ["low_context_precision"],
        "eval_note": "Good coverage of major Python web frameworks. Some irrelevant results in lower ranks. Context precision could be improved by filtering out video content.",
        "eval_fix": "Add domain filters for official docs (python.org, fastapi.tiangolo.com)",
    },
    {
        "call_id": "call_d4e5f6",
        "run_key": _SEED_RUN_KEYS["fastapi_bug"],
        "trace_id": "trace_790",
        "query": "FastAPI async middleware TypeError: 'NoneType' object is not callable",
        "normalized_query": "FastAPI middleware TypeError NoneType not callable fix",
        "research_goal": "Debug a FastAPI middleware error in production",
        "tool_name": "web_search",
        "tool_profile": "default",
        "providers_requested": ["searxng", "tavily"],
        "num_results": 5,
        "final_count": 5,
        "duration_ms": 4120,
        "status": "ok",
        "passed": True,
        "pass_rate_score": 0.91,
        "faithfulness": 0.95,
        "groundedness": 0.92,
        "context_precision": 0.88,
        "context_recall": 0.85,
        "answer_relevance": 0.94,
        "mrr_at_10": 1.00,
        "ndcg_at_10": 0.93,
        "failure_categories": [],
        "eval_note": "Excellent results. Top result is the exact StackOverflow answer for the specific TypeError. Second result is official FastAPI middleware docs.",
        "eval_fix": None,
    },
    {
        "call_id": "call_g7h8i9",
        "run_key": _SEED_RUN_KEYS["rust_vs_go"],
        "trace_id": "trace_791",
        "query": "Rust vs Go for microservices 2026 production experience",
        "normalized_query": "Rust vs Go microservices production comparison 2026",
        "research_goal": "Decision analysis: which language for new microservices infrastructure",
        "tool_name": "web_search",
        "tool_profile": "research",
        "providers_requested": ["searxng", "tavily", "brave", "jina"],
        "num_results": 10,
        "final_count": 6,
        "duration_ms": 5890,
        "status": "partial",
        "passed": False,
        "pass_rate_score": 0.61,
        "faithfulness": 0.72,
        "groundedness": 0.68,
        "context_precision": 0.55,
        "context_recall": 0.62,
        "answer_relevance": 0.59,
        "mrr_at_10": 0.78,
        "ndcg_at_10": 0.65,
        "failure_categories": ["low_context_precision", "low_answer_relevance", "partial_coverage"],
        "eval_note": "Partial results due to Jina timeout. Missing benchmark data and official language documentation. Results are opinion-based rather than data-driven.",
        "eval_fix": "Retry with searxng+tavily+brave only; add domain boost for rust-lang.org and go.dev for official docs",
    },
]

_PROVIDER_RESULTS = {
    _SEED_RUN_KEYS["python_web"]: [
        {"provider": "searxng", "duration_ms": 890, "results_returned": 12, "error": None},
        {"provider": "tavily", "duration_ms": 1200, "results_returned": 8, "error": None},
        {"provider": "brave", "duration_ms": 1500, "results_returned": 6, "error": None},
    ],
    _SEED_RUN_KEYS["fastapi_bug"]: [
        {"provider": "searxng", "duration_ms": 1100, "results_returned": 8, "error": None},
        {"provider": "tavily", "duration_ms": 2900, "results_returned": 5, "error": None},
    ],
    _SEED_RUN_KEYS["rust_vs_go"]: [
        {"provider": "searxng", "duration_ms": 950, "results_returned": 10, "error": None},
        {"provider": "tavily", "duration_ms": 1800, "results_returned": 7, "error": None},
        {"provider": "brave", "duration_ms": 2100, "results_returned": 5, "error": None},
        {"provider": "jina", "duration_ms": 5000, "results_returned": 0, "error": "TimeoutError: Timed out after 5000ms"},
    ],
}

_FINAL_RESULTS = {
    _SEED_RUN_KEYS["python_web"]: [
        {"rank": 1, "title": "FastAPI repo", "url": "https://github.com/fastapi/fastapi", "snippet": "FastAPI is a modern, fast web framework for building APIs with Python 3.8+ based on standard Python type hints.", "score": 0.94, "rerank_method": "bi_encoder", "origin_provider": "searxng"},
        {"rank": 2, "title": "Django docs", "url": "https://docs.djangoproject.com/en/stable/", "snippet": "Django is a high-level Python web framework that encourages rapid development and clean, pragmatic design.", "score": 0.88, "rerank_method": "bi_encoder", "origin_provider": "tavily"},
        {"rank": 3, "title": "Flask docs", "url": "https://flask.palletsprojects.com/en/stable/", "snippet": "Flask is a lightweight WSGI web application framework designed to make getting started quick and easy.", "score": 0.82, "rerank_method": "provider", "origin_provider": "brave"},
        {"rank": 4, "title": "Real Python comparison", "url": "https://realpython.com/fastapi-vs-django-vs-flask/", "snippet": "Comprehensive comparison: FastAPI leads for API-first projects, Django excels for full-stack, Flask is best for microservices.", "score": 0.76, "rerank_method": "provider", "origin_provider": "tavily"},
        {"rank": 5, "title": "SO: FastAPI vs DRF", "url": "https://stackoverflow.com/questions/78901234/fastapi-vs-django-rest-framework", "snippet": "FastAPI provides automatic OpenAPI docs, built-in validation with Pydantic, and async support. DRF has more mature ecosystem.", "score": 0.72, "rerank_method": "diversity", "origin_provider": "brave"},
        {"rank": 6, "title": "HN: FastAPI in Production", "url": "https://news.ycombinator.com/item?id=34567890", "snippet": "Discussion about production experiences with FastAPI. Users report success for API services but note Django remains stronger for full-stack.", "score": 0.65, "rerank_method": "diversity", "origin_provider": "tavily"},
        {"rank": 7, "title": "YouTube: Django vs FastAPI", "url": "https://youtube.com/watch?v=abc", "snippet": "Video tutorial comparing Django and FastAPI for beginners. Less relevant for production decision-making.", "score": 0.52, "rerank_method": "provider", "origin_provider": "brave"},
        {"rank": 8, "title": "Medium: Why I chose Django", "url": "https://medium.com/tech/why-i-chose-django", "snippet": "Personal blog post about choosing Django over FastAPI for a side project. Lacks technical depth.", "score": 0.48, "rerank_method": "diversity", "origin_provider": "tavily"},
    ],
    _SEED_RUN_KEYS["fastapi_bug"]: [
        {"rank": 1, "title": "SO: FastAPI middleware TypeError", "url": "https://stackoverflow.com/questions/73487634/fastapi-middleware-typeerror-nonetype-object-is-not-callable", "snippet": "This error occurs when you register a middleware without the correct callable signature. Make sure your middleware function accepts `request` and `call_next` parameters.", "score": 0.92, "rerank_method": "bi_encoder", "origin_provider": "searxng"},
        {"rank": 2, "title": "FastAPI middleware docs", "url": "https://fastapi.tiangolo.com/tutorial/middleware/", "snippet": "A middleware is a function that works with every request before it is processed by any specific path operation.", "score": 0.88, "rerank_method": "bi_encoder", "origin_provider": "tavily"},
        {"rank": 3, "title": "GitHub: middleware NoneType issue", "url": "https://github.com/fastapi/fastapi/issues/4567", "snippet": "Solution: ensure the middleware function returns `await call_next(request)` and registration is done before defining routes.", "score": 0.81, "rerank_method": "provider", "origin_provider": "searxng"},
        {"rank": 4, "title": "SO: middleware execution order", "url": "https://stackoverflow.com/questions/89012345/fastapi-middleware-order-issues", "snippet": "FastAPI middleware execution follows registration order. CORSMiddleware should be first. Auth middleware runs before body parsing.", "score": 0.73, "rerank_method": "diversity", "origin_provider": "tavily"},
        {"rank": 5, "title": "Dev.to: FastAPI error handling guide", "url": "https://dev.to/fastapi_error_handling", "snippet": "Guide covering common FastAPI errors including middleware, dependency injection, and async gotchas.", "score": 0.67, "rerank_method": "diversity", "origin_provider": "searxng"},
    ],
    _SEED_RUN_KEYS["rust_vs_go"]: [
        {"rank": 1, "title": "HN: Rust vs Go discussion", "url": "https://news.ycombinator.com/item?id=33445566", "snippet": "Go wins on dev speed, deployment simplicity, goroutine concurrency. Rust wins on performance, memory safety, compile-time guarantees.", "score": 0.88, "rerank_method": "bi_encoder", "origin_provider": "searxng"},
        {"rank": 2, "title": "SO: Rust vs Go in 2026", "url": "https://stackoverflow.com/questions/56789012/rust-vs-go-microservices-2026", "snippet": "Go offers faster dev cycles, excellent stdlib for HTTP/gRPC. Rust offers better perf, guaranteed memory safety.", "score": 0.84, "rerank_method": "bi_encoder", "origin_provider": "tavily"},
        {"rank": 3, "title": "r/rust: production experience", "url": "https://reddit.com/r/rust/comments/abcdef/rust_vs_go_production_experience/", "snippet": "Migrated from Go to Rust for API gateway: 40% less resources but 60% more dev time. Worth it for perf-critical paths.", "score": 0.78, "rerank_method": "provider", "origin_provider": "brave"},
        {"rank": 4, "title": "r/golang: why we chose Go", "url": "https://reddit.com/r/golang/comments/ghijkl/why_we_chose_go_over_rust/", "snippet": "Three-month eval chose Go: faster onboarding, excellent tooling, simpler debugging, goroutines sufficient.", "score": 0.74, "rerank_method": "provider", "origin_provider": "tavily"},
        {"rank": 5, "title": "Rust Production Survey 2026", "url": "https://blog.rust-lang.org/2026/01/rust-in-production-survey.html", "snippet": "78% say Rust met/exceeded perf expectations. 65% say dev time longer. Top uses: API gateways, auth services, data pipelines.", "score": 0.69, "rerank_method": "diversity", "origin_provider": "brave"},
        {"rank": 6, "title": "Medium: Rust vs Go in 2026", "url": "https://medium.com/comparison/rust-vs-go-2026", "snippet": "Opinion piece with no benchmarks. Author prefers Go for microservices but acknowledges Rust's performance edge.", "score": 0.55, "rerank_method": "diversity", "origin_provider": "brave"},
    ],
}

_REWRITE_VARIANTS = {
    _SEED_RUN_KEYS["python_web"]: [
        {"variant_index": 0, "kind": "balanced", "query": "best python web framework 2026", "weight": 1.0, "is_selected": True, "final_score": 0.82},
        {"variant_index": 1, "kind": "precision", "query": "python web framework comparison 2026 FastAPI Django Flask", "weight": 0.8, "is_selected": False, "final_score": 0.88},
        {"variant_index": 2, "kind": "balanced", "query": "best python framework for building APIs production 2026", "weight": 0.7, "is_selected": False, "final_score": 0.76},
    ],
    _SEED_RUN_KEYS["fastapi_bug"]: [
        {"variant_index": 0, "kind": "precision", "query": "FastAPI async middleware TypeError: 'NoneType' object is not callable", "weight": 1.0, "is_selected": True, "final_score": 0.91},
        {"variant_index": 1, "kind": "precision", "query": "FastAPI middleware NoneType not callable error fix", "weight": 0.9, "is_selected": False, "final_score": 0.86},
    ],
    _SEED_RUN_KEYS["rust_vs_go"]: [
        {"variant_index": 0, "kind": "balanced", "query": "Rust vs Go for microservices 2026 production experience", "weight": 1.0, "is_selected": True, "final_score": 0.61},
        {"variant_index": 1, "kind": "recall", "query": "Rust Go microservices comparison production 2026 benchmarks", "weight": 0.6, "is_selected": False, "final_score": 0.55},
    ],
}

_HISTORICAL_DAYS = {
    "Mon": 0.71, "Tue": 0.68, "Wed": 0.74, "Thu": 0.79,
    "Fri": 0.73, "Sat": 0.70, "Sun": 0.73,
}

_EXTRA_CALLS = [  # additional recent calls for the table
    {
        "call_id": "call_j0k1l2",
        "run_key": "run_m3n4o5p6",
        "trace_id": "trace_792",
        "query": "Kubernetes pod scheduling GPU optimization tips",
        "research_goal": "Optimize GPU scheduling for ML training pods on K8s",
        "tool_name": "web_search",
        "tool_profile": "default",
        "duration_ms": 3120,
        "status": "ok",
        "passed": True,
        "pass_rate_score": 0.79,
        "failure_categories": [],
        "eval_note": "Good results covering GPU scheduling, node affinity, and resource limits.",
        "eval_fix": None,
    },
    {
        "call_id": "call_m7n8o9",
        "run_key": "run_p0q1r2s3",
        "trace_id": "trace_793",
        "query": "SQLAlchemy async session deadlock postgres",
        "research_goal": "Fix async SQLAlchemy connection pool deadlock in production",
        "tool_name": "web_search",
        "tool_profile": "default",
        "duration_ms": 8700,
        "status": "ok",
        "passed": False,
        "pass_rate_score": 0.45,
        "failure_categories": ["low_context_precision", "low_answer_relevance"],
        "eval_note": "Results returned generic SQLAlchemy tutorials, not the specific deadlock pattern. SearXNG returned stale cache.",
        "eval_fix": "Add domain boost for github.com/sqlalchemy and docs.sqlalchemy.org; increase result count to 15",
    },
    {
        "call_id": "call_n0p1q2",
        "run_key": "run_r3s4t5u6",
        "trace_id": "trace_794",
        "query": "Next.js 15 app router caching behavior explained",
        "research_goal": "Understand Next.js 15 caching changes for migration planning",
        "tool_name": "web_search",
        "tool_profile": "research",
        "duration_ms": 1980,
        "status": "ok",
        "passed": True,
        "pass_rate_score": 0.87,
        "failure_categories": [],
        "eval_note": "Top result is official Next.js docs. Second is Vercel blog post on caching changes. Highly relevant.",
        "eval_fix": None,
    },
    {
        "call_id": "call_q3r4s5",
        "run_key": "run_t6u7v8w9",
        "trace_id": "trace_795",
        "query": "terraform aws lambda cold start optimization",
        "research_goal": "Reduce Lambda cold start times for Terraform-managed Go functions",
        "tool_name": "web_search",
        "tool_profile": "research",
        "duration_ms": 4500,
        "status": "ok",
        "passed": False,
        "pass_rate_score": 0.52,
        "failure_categories": ["low_context_precision", "partial_coverage", "low_faithfulness"],
        "eval_note": "Results focus on Python/Node cold starts, not Go. Missing Terraform-specific provisioned concurrency examples.",
        "eval_fix": "Rewrite query to include 'golang lambda cold start terraform provisioned concurrency'; boost domain for hashicorp.com/terraform",
    },
]

# ---------------------------------------------------------------------------
# Database seeding
# ---------------------------------------------------------------------------

_SEEDED: bool = False


def _ensure_seeded() -> None:
    """Seed DuckDB with simulated search quality data on first run."""
    global _SEEDED
    if _SEEDED:
        return

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))

    try:
        # Check if already seeded
        count = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'search_events'").fetchone()[0]
        if count > 0:
            con.close()
            _SEEDED = True
            return

        _create_schema(con)
        _insert_seed_data(con)

    finally:
        con.close()

    _SEEDED = True


def _create_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS search_events (
            event_id VARCHAR,
            event_name VARCHAR,
            recorded_at TIMESTAMP,
            run_key VARCHAR,
            tool_name VARCHAR,
            phase VARCHAR,
            query VARCHAR,
            normalized_query VARCHAR,
            research_goal VARCHAR,
            provider VARCHAR,
            model VARCHAR,
            duration_ms DOUBLE,
            input_count INTEGER,
            output_count INTEGER,
            trace_id VARCHAR,
            span_id VARCHAR,
            cache_hit VARCHAR,
            payload_json VARCHAR
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS eval_runs (
            eval_run_id VARCHAR,
            created_at TIMESTAMP,
            suite_name VARCHAR,
            evaluator VARCHAR,
            dataset_name VARCHAR,
            prompt_version VARCHAR,
            notes_json VARCHAR,
            payload_json VARCHAR
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS eval_cases (
            eval_case_id VARCHAR,
            eval_run_id VARCHAR,
            recorded_at TIMESTAMP,
            target_tool VARCHAR,
            query VARCHAR,
            expected_behavior VARCHAR,
            expected_output_json VARCHAR,
            labels_json VARCHAR,
            trace_id VARCHAR,
            run_key VARCHAR,
            payload_json VARCHAR
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS eval_observations (
            eval_observation_id VARCHAR,
            eval_run_id VARCHAR,
            eval_case_id VARCHAR,
            recorded_at TIMESTAMP,
            event_name VARCHAR,
            run_key VARCHAR,
            score DOUBLE,
            verdict VARCHAR,
            notes_json VARCHAR,
            payload_json VARCHAR
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS eval_scores (
            score_id VARCHAR,
            eval_run_id VARCHAR,
            eval_case_id VARCHAR,
            recorded_at TIMESTAMP,
            run_key VARCHAR,
            metric_name VARCHAR,
            score_value DOUBLE,
            payload_json VARCHAR
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS eval_judge_calls (
            judge_call_id VARCHAR,
            eval_run_id VARCHAR,
            eval_case_id VARCHAR,
            recorded_at TIMESTAMP,
            run_key VARCHAR,
            judge_model VARCHAR,
            score_value DOUBLE,
            payload_json VARCHAR
        )
    """)


def _insert_seed_data(con: duckdb.DuckDBPyConnection) -> None:
    eval_run_id = f"eval_run_{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC)

    con.execute(
        "INSERT INTO eval_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [eval_run_id, now - timedelta(days=7), "search_quality", "llm_judge", "production_queries", "v1", "{}", "{}"],
    )

    # Build historical pass-rate events (7 days of simulated data)
    for day_offset in range(7):
        day_date = (now - timedelta(days=day_offset)).date()
        day_name = day_date.strftime("%a")
        base_rate = _HISTORICAL_DAYS.get(day_name, 0.70)
        for _ in range(15):  # ~15 evals per day
            passed = (hash(f"{day_date}_{_}") % 100) / 100.0 < base_rate
            obs_id = f"hist_obs_{day_date}_{_}"
            run_key = f"hist_run_{day_date}_{_}"
            case_id = f"hist_case_{day_date}_{_}"
            con.execute(
                "INSERT INTO eval_cases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [case_id, eval_run_id, day_date, "web_search", f"historical query {day_date} #{_}", None, None, "[]", None, run_key, "{}"],
            )
            con.execute(
                "INSERT INTO eval_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [obs_id, eval_run_id, case_id, day_date, "tool.web_search.response", run_key, 0.75, "pass" if passed else "fail", "{}", "{}"],
            )

    # Insert current calls data
    all_calls = _SEED_CALLS + _EXTRA_CALLS
    for call in all_calls:
        eval_case_id = f"eval_{call['call_id']}"
        eval_obs_id = f"obs_{call['call_id']}"
        eval_score_id = f"score_{call['call_id']}"
        judge_call_id = f"judge_{call['call_id']}"

        con.execute(
            "INSERT INTO eval_cases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [eval_case_id, eval_run_id, now, call["tool_name"], call["query"], None, None, "[]", call["trace_id"], call["run_key"], "{}"],
        )

        con.execute(
            "INSERT INTO eval_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [eval_obs_id, eval_run_id, eval_case_id, now, "tool.web_search.response", call["run_key"], call["pass_rate_score"], "pass" if call["passed"] else "fail", json.dumps({"note": call["eval_note"], "fix": call["eval_fix"], "categories": call["failure_categories"]}), "{}"],
        )

        con.execute(
            "INSERT INTO eval_scores VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [eval_score_id, eval_run_id, eval_case_id, now, call["run_key"], "pass_rate", call["pass_rate_score"], "{}"],
        )

        con.execute(
            "INSERT INTO eval_judge_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [judge_call_id, eval_run_id, eval_case_id, now, call["run_key"], "claude-sonnet-4", call["pass_rate_score"], "{}"],
        )

    # Insert search events for the 3 main calls (with payload JSON)
    for call in _SEED_CALLS:
        run_key = call["run_key"]
        now = datetime.now(UTC)
        call_time = now - timedelta(minutes=hash(call["call_id"]) % 60)

        # Main orchestrator response event
        final_results = _FINAL_RESULTS.get(run_key, [])
        con.execute(
            """
            INSERT INTO search_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                f"evt_orch_{call['call_id']}",
                "search.orchestrator.response",
                call_time,
                run_key,
                call["tool_name"],
                "response",
                call["query"],
                call["normalized_query"],
                call["research_goal"],
                ", ".join(call["providers_requested"]),
                None,
                call["duration_ms"],
                call["num_results"],
                call["final_count"],
                call["trace_id"],
                f"span_{call['call_id']}",
                "false",
                json.dumps({
                    "query": call["query"],
                    "research_goal": call["research_goal"],
                    "results": final_results,
                    "merged_results": final_results[:max(0, len(final_results) - 2)],
                    "tool_name": call["tool_name"],
                    "tool_profile": call["tool_profile"],
                    "status": call["status"],
                }),
            ],
        )

        # Provider search result events
        for prov in _PROVIDER_RESULTS.get(run_key, []):
            con.execute(
                """
                INSERT INTO search_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    f"evt_prov_{call['call_id']}_{prov['provider']}",
                    "provider.search.result",
                    call_time - timedelta(seconds=2),
                    run_key,
                    call["tool_name"],
                    "provider_call",
                    call["query"],
                    call["normalized_query"],
                    call["research_goal"],
                    prov["provider"],
                    None,
                    prov["duration_ms"],
                    10,
                    prov["results_returned"],
                    call["trace_id"],
                    f"span_prov_{call['call_id']}_{prov['provider']}",
                    "false",
                    json.dumps({
                        "provider_name": prov["provider"],
                        "query": call["query"],
                        "duration_ms": prov["duration_ms"],
                        "num_results": prov["results_returned"],
                        "results": [{"title": f"Result from {prov['provider']}", "link": f"https://{prov['provider']}.com/result", "snippet": "..."}] * prov["results_returned"],
                        "error_message": prov["error"],
                    }),
                ],
            )

        # Query rewrite event
        variants = _REWRITE_VARIANTS.get(run_key, [])
        if variants:
            con.execute(
                """
                INSERT INTO search_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    f"evt_rw_{call['call_id']}",
                    "query.rewrite.completed",
                    call_time - timedelta(seconds=5),
                    run_key,
                    call["tool_name"],
                    "rewrite",
                    call["query"],
                    call["normalized_query"],
                    call["research_goal"],
                    None,
                    "cerebras/gpt-oss-120b",
                    320.0,
                    1,
                    len(variants),
                    call["trace_id"],
                    f"span_rw_{call['call_id']}",
                    "false",
                    json.dumps({
                        "query": call["query"],
                        "variants": variants,
                        "selected_index": next(i for i, v in enumerate(variants) if v["is_selected"]),
                    }),
                ],
            )

        # Rerank event (if results >= 5)
        if len(final_results) >= 5:
            con.execute(
                """
                INSERT INTO search_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    f"evt_rr_{call['call_id']}",
                    "search.rerank.summary",
                    call_time - timedelta(seconds=1),
                    run_key,
                    call["tool_name"],
                    "rerank",
                    call["query"],
                    call["normalized_query"],
                    call["research_goal"],
                    "voyage",
                    "rerank-2.5",
                    180.0,
                    len(final_results) + 2,
                    len(final_results),
                    call["trace_id"],
                    f"span_rr_{call['call_id']}",
                    "false",
                    json.dumps({
                        "query": call["query"],
                        "provider": "voyage",
                        "model": "rerank-2.5",
                        "results": final_results,
                        "input_count": len(final_results) + 2,
                        "output_count": len(final_results),
                    }),
                ],
            )

        # Error event for failed calls
        if call["status"] == "partial":
            for prov in _PROVIDER_RESULTS.get(run_key, []):
                if prov.get("error"):
                    con.execute(
                        """
                        INSERT INTO search_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            f"evt_err_{call['call_id']}_{prov['provider']}",
                            "tool.error.classified",
                            call_time - timedelta(seconds=1),
                            run_key,
                            call["tool_name"],
                            "error",
                            call["query"],
                            call["normalized_query"],
                            call["research_goal"],
                            prov["provider"],
                            None,
                            prov["duration_ms"],
                            0,
                            0,
                            call["trace_id"],
                            f"span_err_{call['call_id']}_{prov['provider']}",
                            "false",
                            json.dumps({
                                "error_type": "TimeoutError",
                                "provider": prov["provider"],
                                "provider_name": prov["provider"],
                                "tool_name": call["tool_name"],
                                "status_code": "408",
                                "action": "retry_without_jina",
                            }),
                        ],
                    )


# ---------------------------------------------------------------------------
# Cached query functions — the dashboard data layer
# ---------------------------------------------------------------------------

def _query(sql: str, params: list | None = None) -> pd.DataFrame:
    """Run a read-only query against the DuckDB file."""
    _ensure_seeded()
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return con.execute(sql, params).df() if params else con.execute(sql).df()
    finally:
        con.close()


def get_pass_rate_7d() -> pd.DataFrame:
    return _query("""
        WITH daily AS (
            SELECT
                recorded_at::DATE AS day,
                count(*) FILTER (WHERE verdict = 'pass') * 1.0 / nullif(count(*), 0) AS pass_rate,
                count(*) AS total_evals
            FROM eval_observations
            WHERE recorded_at >= CURRENT_DATE - INTERVAL '8 days'
            GROUP BY recorded_at::DATE
        )
        SELECT day, pass_rate, total_evals
        FROM daily
        ORDER BY day
    """)


def get_headline_kpi() -> dict:
    df = _query("""
        SELECT
            count(*) FILTER (WHERE verdict = 'pass') * 1.0 / nullif(count(*), 0) AS pass_rate,
            count(*) AS total_evals,
            count(*) FILTER (WHERE verdict = 'fail') AS failures
        FROM eval_observations
        WHERE recorded_at >= CURRENT_DATE - INTERVAL '7 days'
    """)
    prior = _query("""
        SELECT
            count(*) FILTER (WHERE verdict = 'pass') * 1.0 / nullif(count(*), 0) AS pass_rate
        FROM eval_observations
        WHERE recorded_at >= CURRENT_DATE - INTERVAL '14 days'
          AND recorded_at < CURRENT_DATE - INTERVAL '7 days'
    """)
    row = df.iloc[0]
    current_rate = row["pass_rate"] if pd.notna(row["pass_rate"]) else 0.0
    prior_val = prior.iloc[0]["pass_rate"] if not prior.empty and pd.notna(prior.iloc[0]["pass_rate"]) else None
    prior_rate = prior_val if prior_val is not None else current_rate
    has_prior = prior_val is not None
    return {
        "pass_rate": round(float(current_rate) * 100, 1),
        "total_evals": int(row["total_evals"]),
        "failures": int(row["failures"]),
        "delta": round((float(current_rate) - float(prior_rate)) * 100, 1) if has_prior else None,
    }


def get_failure_clusters() -> pd.DataFrame:
    return _query("""
        WITH parsed AS (
            SELECT
                json_extract_string(notes_json, '$.categories') AS categories_raw,
                json_extract_string(notes_json, '$.note') AS note_text,
                json_extract_string(notes_json, '$.fix') AS fix_text
            FROM eval_observations
            WHERE verdict = 'fail'
              AND notes_json IS NOT NULL
        ),
        unrolled AS (
            SELECT
                trim(unnest(string_split(trim(categories_raw, '[]" '), '","'))) AS category,
                note_text,
                fix_text
            FROM parsed
            WHERE categories_raw IS NOT NULL AND categories_raw != ''
        )
        SELECT
            category,
            count(*) AS failure_count,
            first(note_text) FILTER (WHERE note_text IS NOT NULL AND note_text != '') AS example_note,
            first(fix_text) FILTER (WHERE fix_text IS NOT NULL AND fix_text != '') AS suggested_fix
        FROM unrolled
        WHERE category IS NOT NULL AND category != ''
        GROUP BY category
        ORDER BY failure_count DESC
    """)


def get_recent_calls(limit: int = 10) -> pd.DataFrame:
    return _query(f"""
        WITH recent AS (
            SELECT DISTINCT ON (run_key)
                e.event_id,
                e.recorded_at,
                e.run_key,
                e.query,
                e.research_goal,
                e.tool_name,
                coalesce(e.provider, json_extract_string(e.payload_json, '$.tool_profile')) AS tool_profile,
                e.duration_ms,
                json_extract_string(e.payload_json, '$.status') AS status,
                e.output_count AS final_count
            FROM search_events e
            WHERE e.event_name = 'search.orchestrator.response'
            ORDER BY e.run_key, e.recorded_at DESC
        )
        SELECT
            r.*,
            o.verdict,
            o.score,
            json_extract_string(o.notes_json, '$.note') AS eval_note,
            json_extract_string(o.notes_json, '$.fix') AS eval_fix,
            json_extract_string(o.notes_json, '$.categories') AS failure_categories
        FROM recent r
        LEFT JOIN eval_observations o ON o.run_key = r.run_key
        ORDER BY r.recorded_at DESC
        LIMIT {limit}
    """)


def get_call_detail(run_key: str) -> dict | None:
    """Return everything needed for the Call Detail page."""
    call_df = _query("""
        SELECT
            e.event_id, e.recorded_at, e.run_key, e.query, e.normalized_query,
            e.research_goal, e.tool_name, e.duration_ms,
            coalesce(e.provider, json_extract_string(e.payload_json, '$.tool_profile')) AS tool_profile,
            json_extract_string(e.payload_json, '$.status') AS status,
            json_extract_string(e.payload_json, '$.providers_requested') AS providers_requested_text,
            e.output_count AS final_count,
            json_extract(e.payload_json, '$.num_results') AS num_results_raw,
            e.payload_json
        FROM search_events e
        WHERE e.event_name = 'search.orchestrator.response'
          AND e.run_key = ?
        LIMIT 1
    """, [run_key])

    if call_df.empty:
        return None

    row = call_df.iloc[0]
    payload = json.loads(row["payload_json"]) if row["payload_json"] else {}

    # Eval data
    eval_df = _query("""
        SELECT verdict, score, notes_json
        FROM eval_observations
        WHERE run_key = ?
        LIMIT 1
    """, [run_key])

    verdict = None
    eval_score = None
    eval_note = None
    eval_fix = None
    failure_categories = []
    if not eval_df.empty:
        ev = eval_df.iloc[0]
        verdict = ev["verdict"]
        eval_score = ev["score"]
        if ev["notes_json"]:
            try:
                notes = json.loads(ev["notes_json"])
                eval_note = notes.get("note")
                eval_fix = notes.get("fix")
                failure_categories = notes.get("categories", [])
            except (json.JSONDecodeError, TypeError):
                pass

    # Provider events
    providers_df = _query("""
        SELECT
            coalesce(provider, json_extract_string(payload_json, '$.provider_name')) AS provider_name,
            CAST(coalesce(
                CAST(duration_ms AS VARCHAR),
                json_extract_string(payload_json, '$.duration_ms')
            ) AS DOUBLE) AS provider_duration_ms,
            CAST(coalesce(
                json_extract_string(payload_json, '$.num_results'),
                json_extract_string(payload_json, '$.results_returned'),
                '0'
            ) AS INTEGER) AS results_returned,
            json_extract_string(payload_json, '$.error_message') AS error_message
        FROM search_events
        WHERE event_name = 'provider.search.result'
          AND run_key = ?
        ORDER BY provider_name
    """, [run_key])

    # Rewrite variants
    rewrites_df = _query("""
        SELECT
            CAST(v.key AS INTEGER) AS variant_index,
            json_extract_string(v.value, '$.kind') AS kind,
            json_extract_string(v.value, '$.query') AS variant_query,
            CAST(coalesce(json_extract_string(v.value, '$.weight'), '1.0') AS DOUBLE) AS weight,
            CAST(json_extract_string(v.value, '$.is_selected') AS BOOLEAN) AS is_selected,
            CAST(coalesce(json_extract_string(v.value, '$.final_score'), '0') AS DOUBLE) AS final_score
        FROM search_events e,
             json_each(json_extract(e.payload_json, '$.variants')) AS v
        WHERE e.event_name = 'query.rewrite.completed'
          AND e.run_key = ?
        ORDER BY variant_index
    """, [run_key])

    # Results from payload
    results = payload.get("results", []) or payload.get("merged_results", [])
    merged_results = payload.get("merged_results", [])

    return {
        "query": str(row["query"] or ""),
        "normalized_query": str(row["normalized_query"] or ""),
        "research_goal": str(row["research_goal"] or ""),
        "recorded_at": row["recorded_at"],
        "duration_ms": int(row["duration_ms"] or 0),
        "tool_profile": str(row["tool_profile"] or "unknown"),
        "status": str(row["status"] or "unknown"),
        "final_count": int(row["final_count"] or 0),
        "verdict": verdict,
        "eval_score": eval_score,
        "eval_note": eval_note,
        "eval_fix": eval_fix,
        "failure_categories": failure_categories,
        "results": results,
        "merged_results": merged_results,
        "providers": providers_df.to_dict("records") if not providers_df.empty else [],
        "rewrites": rewrites_df.to_dict("records") if not rewrites_df.empty else [],
    }
