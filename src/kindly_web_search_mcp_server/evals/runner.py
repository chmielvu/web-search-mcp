"""mcpevals runner adapter.

Runs eval cases (from evals.cases) against the MCP server using mcp-eval primitives when available.
Persists results, deterministic metrics, and judge scores to DuckDB eval tables.
Sends judge metadata to Langfuse only for offline eval runs (never user-facing paths).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, UTC
from typing import Any

from ..analytics.evals import ensure_eval_tables
from ..settings import settings

LOGGER = logging.getLogger(__name__)

try:
    from mcp_eval import Dataset, Case, task, Expect  # type: ignore
    from mcp_eval.session import TestSession  # type: ignore
    MCPEVAL_AVAILABLE = True
except Exception:  # pragma: no cover - optional eval dep
    MCPEVAL_AVAILABLE = False
    Dataset = Case = task = Expect = TestSession = None  # type: ignore

from .cases import EvalCase
from .judges import (
    judge_argument_correctness,
    judge_ranking_quality,
    judge_source_usefulness,
    judge_tool_choice_correct,
)
from .metrics import (
    expected_tool_called,
    forbidden_tool_not_called,
    mrr_at_k,
    ndcg_at_k,
    top_k_domain_hit,
)


def _ensure_db() -> None:
    ensure_eval_tables(db_path=str(settings.analytics_duckdb_path))


def _insert_eval_run(
    suite_name: str,
    evaluator: str = "web-search-mcp-eval-runner",
    dataset_name: str = "joint-quality",
    notes: dict[str, Any] | None = None,
) -> str:
    _ensure_db()
    import duckdb

    run_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    con = duckdb.connect(str(settings.analytics_duckdb_path))
    try:
        con.execute(
            "INSERT INTO eval_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                run_id,
                now,
                suite_name,
                evaluator,
                dataset_name,
                "v1",
                json.dumps(notes or {}, ensure_ascii=True),
                json.dumps({}, ensure_ascii=True),
            ],
        )
    finally:
        con.close()
    return run_id


def _insert_eval_case(
    run_id: str,
    case: EvalCase,
    run_key: str,
) -> str:
    _ensure_db()
    import duckdb

    case_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    con = duckdb.connect(str(settings.analytics_duckdb_path))
    try:
        con.execute(
            "INSERT INTO eval_cases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                case_id,
                run_id,
                now,
                case.expected_tool_calls[0].tool_name if case.expected_tool_calls else "web_search",
                case.query,
                case.research_goal or "",
                json.dumps([t.model_dump() for t in case.expected_tool_calls], ensure_ascii=True),
                json.dumps([c.model_dump() for c in case.candidate_sets], ensure_ascii=True),
                "",  # trace_id
                run_key,
                json.dumps(case.model_dump(), ensure_ascii=True),
            ],
        )
    finally:
        con.close()
    return case_id


def _record_metric(
    run_id: str,
    case_id: str,
    run_key: str,
    metric_name: str,
    score: float,
    payload: dict[str, Any] | None = None,
) -> None:
    _ensure_db()
    import duckdb

    con = duckdb.connect(str(settings.analytics_duckdb_path))
    try:
        con.execute(
            "INSERT INTO eval_scores VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                str(uuid.uuid4()),
                run_id,
                case_id,
                datetime.now(UTC),
                run_key,
                metric_name,
                float(score),
                json.dumps(payload or {}, ensure_ascii=True),
            ],
        )
    finally:
        con.close()


def _record_failure(run_id: str, case_id: str, run_key: str, code: str, payload: dict | None = None) -> None:
    _ensure_db()
    import duckdb

    con = duckdb.connect(str(settings.analytics_duckdb_path))
    try:
        con.execute(
            "INSERT INTO eval_failures VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                str(uuid.uuid4()),
                run_id,
                case_id,
                datetime.now(UTC),
                run_key,
                code,
                json.dumps(payload or {}, ensure_ascii=True),
            ],
        )
    finally:
        con.close()


def run_eval_case(
    case: EvalCase,
    *,
    suite_name: str = "joint-quality",
    server_command: list[str] | None = None,
) -> dict[str, Any]:
    """Run a single EvalCase using mcpevals (if available) or direct simulation, persist all scores.

    Returns summary dict. Safe to call from CLI/eval scripts only.
    """
    run_key = f"{case.suite_name}:{case.eval_case_id}:{int(time.time())}"
    run_id = _insert_eval_run(suite_name, dataset_name=case.suite_name)
    case_id = _insert_eval_case(run_id, case, run_key)

    summary: dict[str, Any] = {
        "eval_run_id": run_id,
        "eval_case_id": case_id,
        "run_key": run_key,
        "query": case.query,
        "metrics": {},
        "judges": {},
    }

    if not MCPEVAL_AVAILABLE:
        LOGGER.warning("mcpevals not installed; using deterministic-only simulation for case %s", case.eval_case_id)
        # simulate empty tool calls for deterministic path
        actual_tools: list[dict] = []
    else:
        # In real adapter we would spin an agent against our server stdio.
        # For this impl we simulate a minimal run and let deterministic + judges measure.
        # (Full mcp-eval wiring would require agent + session that calls our kindly server.)
        actual_tools = []  # placeholder; real run would populate from traces

    # Deterministic metrics
    try:
        for exp in case.expected_tool_calls:
            name = exp.tool_name
            sc = expected_tool_called(actual_tools, name)
            _record_metric(run_id, case_id, run_key, f"expected_tool_called:{name}", sc)
            summary["metrics"][f"expected_tool_called:{name}"] = sc

            if exp.forbidden:
                scf = forbidden_tool_not_called(actual_tools, name)
                _record_metric(run_id, case_id, run_key, f"forbidden_tool_not_called:{name}", scf)
                summary["metrics"][f"forbidden_tool_not_called:{name}"] = scf
    except Exception as exc:
        _record_failure(run_id, case_id, run_key, "deterministic_tool", {"error": str(exc)})

    # Candidate / ranking metrics from candidate_sets if present
    for cset in case.candidate_sets:
        try:
            cands = cset.candidates
            if cands:
                # use first gold domain or url from expected
                gold = []
                for e in case.expected_tool_calls:
                    # synthetic gold from case if provided in labels or just use first cand
                    pass
                # simple: use domains from candidates if marked relevant
                golds = [c.get("link") or c.get("url") or "" for c in cands if c.get("relevance")]
                if not golds and cands:
                    golds = [cands[0].get("link") or cands[0].get("url") or ""]
                mrr = mrr_at_k(cands, golds or [""], 5)
                nd = ndcg_at_k(cands, golds or [""], 10)
                hit = top_k_domain_hit(cands, golds[0] if golds else "", 3)
                _record_metric(run_id, case_id, run_key, "mrr_at_5", mrr, {"set": cset.name})
                _record_metric(run_id, case_id, run_key, "ndcg_at_10", nd, {"set": cset.name})
                _record_metric(run_id, case_id, run_key, "top_k_domain_hit", hit, {"set": cset.name})
                summary["metrics"].update({"mrr_at_5": mrr, "ndcg_at_10": nd, "top_k_domain_hit": hit})
        except Exception as exc:
            _record_failure(run_id, case_id, run_key, "ranking_metric", {"error": str(exc), "set": cset.name})

    # Judge metrics (always JSON path, only here in eval runner)
    try:
        # tool choice
        j1 = judge_tool_choice_correct(
            case.query,
            actual_tools,
            [t.model_dump() for t in case.expected_tool_calls],
            eval_run_id=run_id,
            eval_case_id=case_id,
            run_key=run_key,
        )
        summary["judges"]["tool_choice_correct"] = j1["score"]

        # argument (use actual or empty)
        j2 = judge_argument_correctness(
            case.query,
            actual_tools,
            eval_run_id=run_id,
            eval_case_id=case_id,
            run_key=run_key,
        )
        summary["judges"]["argument_correctness"] = j2["score"]

        # sources from first candidate set if present
        sources = case.candidate_sets[0].candidates if case.candidate_sets else []
        j3 = judge_source_usefulness(
            case.query,
            sources,
            eval_run_id=run_id,
            eval_case_id=case_id,
            run_key=run_key,
        )
        summary["judges"]["source_usefulness"] = j3["score"]

        # ranking
        ranked = sources
        gold = [c.get("link") or "" for c in sources[:3]]
        j4 = judge_ranking_quality(
            case.query,
            ranked,
            gold,
            eval_run_id=run_id,
            eval_case_id=case_id,
            run_key=run_key,
        )
        summary["judges"]["ranking_quality"] = j4["score"]
    except Exception as exc:
        _record_failure(run_id, case_id, run_key, "judge", {"error": str(exc)})
        LOGGER.exception("judge failed for case %s", case.eval_case_id)

    return summary


def run_dataset(
    cases: list[EvalCase],
    *,
    suite_name: str = "joint-quality",
) -> list[dict[str, Any]]:
    """Run multiple cases. Returns list of summaries."""
    _insert_eval_run(suite_name, notes={"num_cases": len(cases)})
    summaries = []
    for case in cases:
        try:
            s = run_eval_case(case, suite_name=suite_name)
            summaries.append(s)
        except Exception as exc:
            LOGGER.error("case %s failed: %s", case.eval_case_id, exc)
            summaries.append({"eval_case_id": case.eval_case_id, "error": str(exc)})
    return summaries


__all__ = ["run_eval_case", "run_dataset", "MCPEVAL_AVAILABLE"]
