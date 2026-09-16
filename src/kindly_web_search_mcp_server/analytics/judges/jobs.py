"""Parallel-facet job primitives for the judge pipeline.

Owns the two per-result job dataclasses (``_RerankImprovementJob``,
``_ResultQualityJob``) and the parallel worker (``_run_parallel_facet``)
that each runs on its own DuckDB connection. ``judge_search_run``
builds the job list and submits it to a fresh executor; the workers
themselves never call back into the orchestrator, so this module
sits at the bottom of the package import graph (no cycles).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .digest import _ctx
from .persistence import _connect, _ensure_loaded, _store_judgment_row
from .stages import _JUDGE_MODEL, _parse_result, _run_prompt

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _RerankImprovementJob:
    run_key: str
    stage_name: str
    query: str
    cand_rows: list[tuple]
    db_path: str | None


@dataclass(frozen=True, slots=True)
class _ResultQualityJob:
    run_key: str
    query: str
    research_goal: str
    intent: str
    rank: object
    title: str
    link: str
    snippet: str
    db_path: str | None


def _run_parallel_facet(job: _RerankImprovementJob | _ResultQualityJob) -> int:
    """Run one independent per-result facet on its own DuckDB connection.

    Returns 1 when a judgment row is written, else 0.
    """
    connection = _connect(job.db_path)
    try:
        if not _ensure_loaded(connection):
            return 0
        if isinstance(job, _RerankImprovementJob):
            before_lines = [f"  {rb}. {link}" for rb, _ra, _s, link in job.cand_rows]
            after_lines = [
                f"  {ra}. {link}  (survived={bool(surv)})" for _rb, ra, surv, link in job.cand_rows
            ]
            rerank_ctx = _ctx(
                ("query", job.query),
                ("stage", job.stage_name),
                ("before", "\n".join(before_lines) if before_lines else "(no rows)"),
                ("after", "\n".join(after_lines) if after_lines else "(no rows)"),
            )
            raw, duration = _run_prompt(
                connection,
                model_name=_JUDGE_MODEL,
                prompt_name="judge_rerank_improvement",
                context_columns=rerank_ctx,
            )
            _store_judgment_row(
                connection,
                run_key=job.run_key,
                judgment_kind="rerank_improvement",
                judgment_target=job.stage_name,
                prompt_name="judge_rerank_improvement",
                model_name=_JUDGE_MODEL,
                raw=raw,
                duration=duration,
                parsed=_parse_result(raw),
                context_columns=rerank_ctx,
                build_verdict=lambda p: str(p.get("verdict") or ""),
                build_reasoning=lambda p: str(p.get("reasoning") or ""),
            )
            return 1

        rq_ctx = _ctx(
            ("query", job.query),
            ("research_goal", job.research_goal),
            ("intent", job.intent),
            ("rank", "" if job.rank is None else str(job.rank)),
            ("title", job.title),
            ("snippet", job.snippet),
        )
        raw, duration = _run_prompt(
            connection,
            model_name=_JUDGE_MODEL,
            prompt_name="judge_result_quality",
            context_columns=rq_ctx,
        )

        def _rq_verdict(p: dict) -> str:
            return (
                f"intent_match={bool(p.get('intent_match'))}; "
                f"informativeness={int(p.get('informativeness') or 0)}"
            )

        _store_judgment_row(
            connection,
            run_key=job.run_key,
            judgment_kind="result_quality",
            judgment_target=job.link,
            prompt_name="judge_result_quality",
            model_name=_JUDGE_MODEL,
            raw=raw,
            duration=duration,
            parsed=_parse_result(raw),
            context_columns=rq_ctx,
            build_verdict=_rq_verdict,
            build_reasoning=lambda p: str(p.get("reasoning") or ""),
        )
        return 1
    except Exception:
        logger.exception("parallel facet %s failed", type(job).__name__)
        return 0
    finally:
        connection.close()
