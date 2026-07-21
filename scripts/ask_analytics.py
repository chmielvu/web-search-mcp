"""Natural-language → SQL helper for the analytics database.

Example:
    python scripts/ask_analytics.py "How many searches failed yesterday?"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

import duckdb  # noqa: E402
from openai import OpenAI  # noqa: E402

from kindly_web_search_mcp_server.analytics.duckdb_store import _db_path  # noqa: E402
from kindly_web_search_mcp_server.settings import settings  # noqa: E402

_SYSTEM_PROMPT = """You are a DuckDB SQL expert for the web-search-mcp analytics database.

Key tables and views:
- search_runs — one row per search run with query, intent, status, duration_ms, result counts, rewrite metadata.
- search_branches — one row per branch per run.
- provider_calls — one outbound provider call per row.
- search_candidates — deduplicated merged candidates.
- rerank_stages — rerank stage summaries per run.
- rerank_candidates — candidate survival per stage.
- final_results — returned results.
- query_embeddings / candidate_embeddings — FLOAT[1024] vectors.
- judge_evaluations — LLM-as-judge 4D scores.
- llm_call_log — unified cost tracking.
- Views: vw_run_summary, vw_provider_performance, vw_candidate_funnel, vw_rerank_timeline,
  vw_rewrite_diagnostics, vw_daily_trend, vw_quality_distribution, vw_provider_health,
  vw_judge_quality, vw_end_to_end_quality, vw_cost_attribution, vw_embedding_similarity.

Rules:
- Emit a single SELECT statement only. No DDL, no DML, no CTEs that write data.
- Use `main.` prefix for tables/views, e.g. `FROM main.search_runs`.
- Prefer views when the question matches their purpose.
- Add a sensible LIMIT (max 100).
- Cast dates with `::DATE` or use `date_trunc('day', recorded_at)`.
- For similarity use `array_cosine_distance(q.embedding, c.embedding)`.

Return ONLY valid JSON in this exact shape:
{"sql": "...", "rationale": "..."}
"""


def _get_openai_client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("WORKER_OPENAI_API_KEY")
    base_url = os.environ.get("OPENAI_API_BASE") or os.environ.get("WORKER_OPENAI_BASE_URL")
    if not api_key:
        raise RuntimeError("Set OPENAI_API_KEY or WORKER_OPENAI_API_KEY in your environment.")
    return OpenAI(api_key=api_key, base_url=base_url, timeout=60)


def _nl_to_sql(question: str) -> dict:
    """Ask the configured LLM to turn a natural-language question into SQL."""
    client = _get_openai_client()
    model = os.environ.get("ASK_ANALYTICS_MODEL", "gpt-4o-mini")
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
        temperature=0.0,
    )
    content = response.choices[0].message.content or "{}"
    # Strip fenced code markers if present
    content = content.strip()
    if content.startswith("```"):
        content = "\n".join(line for line in content.splitlines() if not line.startswith("```"))
    return json.loads(content)


def _run_readonly(sql: str) -> list[dict]:
    path = _db_path()
    if not path.exists():
        return []
    conn = duckdb.connect(str(path), read_only=True)
    try:
        result = conn.execute(sql)
        cols = [d[0] for d in result.description]
        return [dict(zip(cols, row)) for row in result.fetchall()]
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ask the analytics database a question in plain English."
    )
    parser.add_argument("question", help="Natural-language question")
    parser.add_argument("--max-rows", type=int, default=100, help="Maximum rows to return")
    args = parser.parse_args()

    if not settings.analytics_enabled:
        print("Analytics are disabled in settings.", file=sys.stderr)
        return 1

    plan = _nl_to_sql(args.question)
    sql = plan.get("sql", "")
    if "LIMIT" not in sql.upper():
        sql = f"{sql} LIMIT {args.max_rows}"

    rows = _run_readonly(sql)
    print(
        json.dumps(
            {
                "question": args.question,
                "sql": sql,
                "rationale": plan.get("rationale", ""),
                "rows": rows,
                "row_count": len(rows),
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
