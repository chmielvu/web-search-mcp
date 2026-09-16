"""The judge orchestration module.

Owns the public ``judge_search_run`` function that fires all six
facet-decomposed judgments for a single search run, and the
public ``schedule_judge_search_run`` that submits a judge job to the
shared fire-and-forget executor with shutdown-resilience fallbacks.
Both functions live here so the package import graph is acyclic:
``.jobs`` provides the per-facet primitives (no dependency back here),
this module depends on ``.jobs``, ``.executor``, ``.persistence``,
``.stages``, and ``.digest`` only, and ``__init__`` imports everything
eagerly.
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, as_completed
from pathlib import Path

from ...settings import settings
from ..writers.connection import _db_path
from .digest import (
    _REWRITE_STRATEGIES,
    _build_run_digest,
    _ctx,
    _fetch_branch_errors,
    _format_overview_reasoning,
)
from .executor import (
    _DaemonThreadPoolExecutor,
    _JUDGE_LIFECYCLE,
    _JUDGE_SCHEDULE_LOCK,
    _PENDING_JUDGE_FUTURES,
    _PENDING_JUDGE_FUTURES_LOCK,
    _get_judge_executor,
)
from .jobs import _ResultQualityJob, _RerankImprovementJob, _run_parallel_facet
from .persistence import _connect, _ensure_loaded, _store_judgment_row
from .stages import _JUDGE_MODEL, _parse_result, _run_prompt

logger = logging.getLogger(__name__)


def judge_search_run(
    run_key: str,
    *,
    db_path: str | None = None,
) -> int:
    """Run all six facet-decomposed FlockMTL prompts against the given search run.

    Returns the number of judgment rows persisted. Returns 0 when:
      - `settings.flockmtl_enabled` is False
      - FlockMTL install/load fails (community catalog unreachable)
      - the run_key doesn't exist in `search_runs`

    Each facet call's failure is caught and persisted as a
    `status='error'` row so the orchestrator never crashes the calling
    search pipeline. Per-rubric blindness: `judge_run_overview`,
    `judge_rerank_improvement`, and `judge_result_quality` SELECTs are
    explicit whitelists that never read reranker scores, matching the
    blindness statements in their prompt templates.

    Args:
        run_key: The run_key of the search to judge.
        db_path: Optional analytics DuckDB path override (testing).
    """
    if not settings.flockmtl_enabled:
        logger.debug("flockmtl_enabled=false, skipping judge for %s", run_key)
        return 0

    path = Path(_db_path(db_path))
    if not path.exists():
        logger.warning("analytics DB missing at %s, skipping judge", path)
        return 0

    connection = _connect(db_path)
    try:
        if not _ensure_loaded(connection):
            logger.warning("FlockMTL load failed, skipping judge for %s", run_key)
            return 0

        run_row = connection.execute(
            "SELECT query, intent, status, error_type, selected_providers, "
            "final_result_count, rewritten_branch_queries, research_goal, "
            "understanding_confidence, rewrite_enabled "
            "FROM search_runs WHERE run_key = ?",
            [run_key],
        ).fetchone()
        if not run_row:
            logger.warning("run_key=%s not found in search_runs", run_key)
            return 0

        (
            query,
            intent,
            status,
            error_type,
            selected_providers,
            final_count,
            rewritten_queries,
            research_goal,
            understanding_confidence,
            rewrite_enabled,
        ) = run_row
        providers_str = ", ".join(selected_providers) if selected_providers else "none"
        # DuckDB returns VARCHAR[] as a list[Optional[str]] when read via
        # pyarrow=False; normalize to plain list[str] or empty list.
        rewrites: list[str] = [str(q) for q in (rewritten_queries or []) if q]
        judgments_written = 0

        # (a) judge_run_overview -- always fires FIRST (1 call, judge_quality).
        # Firing first means the overview is independent of the five
        # diagnostic facets (no circularity -- it reads raw run data,
        # not their verdicts). Blindness: the digest contains NO raw
        # reranker scores (see _build_run_digest + SELECT whitelist).
        digest = _build_run_digest(connection, run_key)
        overview_ctx = _ctx(("run_digest", digest))
        raw, duration = _run_prompt(
            connection,
            model_name=_JUDGE_MODEL,
            prompt_name="judge_run_overview",
            context_columns=overview_ctx,
        )
        _store_judgment_row(
            connection,
            run_key=run_key,
            judgment_kind="run_overview",
            judgment_target=run_key,
            prompt_name="judge_run_overview",
            model_name=_JUDGE_MODEL,
            raw=raw,
            duration=duration,
            parsed=_parse_result(raw),
            context_columns=overview_ctx,
            build_verdict=lambda p: str(p.get("verdict") or ""),
            build_reasoning=_format_overview_reasoning,
        )
        judgments_written += 1

        # (b) judge_intent_coherence -- always fires (1 call, judge_quality).
        intent_ctx = _ctx(
            ("query", query or ""),
            ("research_goal", research_goal or ""),
            ("intent", intent or "unknown"),
            (
                "understanding_confidence",
                str(understanding_confidence)
                if understanding_confidence is not None and understanding_confidence > 0
                else "unavailable",
            ),
            ("rewrites", "\n".join(rewrites) if rewrites else ""),
        )
        raw, duration = _run_prompt(
            connection,
            model_name=_JUDGE_MODEL,
            prompt_name="judge_intent_coherence",
            context_columns=intent_ctx,
        )
        _store_judgment_row(
            connection,
            run_key=run_key,
            judgment_kind="intent_coherence",
            judgment_target=run_key,
            prompt_name="judge_intent_coherence",
            model_name=_JUDGE_MODEL,
            raw=raw,
            duration=duration,
            parsed=_parse_result(raw),
            context_columns=intent_ctx,
            build_verdict=lambda p: str(p.get("verdict") or ""),
            build_reasoning=lambda p: str(p.get("reasoning") or ""),
        )
        judgments_written += 1

        # (c) judge_rewrite_coverage -- fires only when rewrite_enabled
        # truthy AND rewrites non-empty (1 call, judge_quality).
        if rewrite_enabled and rewrites:
            variants_lines = []
            for i, variant in enumerate(rewrites[:5]):
                strat = _REWRITE_STRATEGIES[i] if i < len(_REWRITE_STRATEGIES) else "unknown"
                variants_lines.append(f"  {i + 1}. {variant}  [{strat}]")
            coverage_ctx = _ctx(
                ("query", query or ""),
                ("research_goal", research_goal or ""),
                ("variants", "\n".join(variants_lines)),
            )
            raw, duration = _run_prompt(
                connection,
                model_name=_JUDGE_MODEL,
                prompt_name="judge_rewrite_coverage",
                context_columns=coverage_ctx,
            )

            def _coverage_verdict(p: dict) -> str:
                rewrites_slice = rewrites[:5]
                return f"covered={p.get('covered_count', 0)}/{len(rewrites_slice)}; redundant={bool(p.get('redundant'))}"

            _store_judgment_row(
                connection,
                run_key=run_key,
                judgment_kind="rewrite_coverage",
                judgment_target="rewrite:set",
                prompt_name="judge_rewrite_coverage",
                model_name=_JUDGE_MODEL,
                raw=raw,
                duration=duration,
                parsed=_parse_result(raw),
                context_columns=coverage_ctx,
                build_verdict=_coverage_verdict,
                build_reasoning=lambda p: str(p.get("reasoning") or ""),
            )
            judgments_written += 1
        # its own short-lived DuckDB connection (connections are not shared
        # across threads). Cap workers at 4 to avoid unbounded HF QPS.
        stage_rows = connection.execute(
            "SELECT stage FROM rerank_stages WHERE run_key = ? ORDER BY recorded_at",
            [run_key],
        ).fetchall()
        stage_payloads: list[tuple[str, list[tuple]]] = []
        for (stage_name,) in stage_rows:
            cand_rows = connection.execute(
                "SELECT rank_before, rank_after, survived, link "
                "FROM rerank_candidates WHERE run_key = ? AND stage = ? "
                "ORDER BY rank_after LIMIT 10",
                [run_key, stage_name],
            ).fetchall()
            stage_payloads.append((stage_name or "", list(cand_rows)))

        result_rows = connection.execute(
            "SELECT rank, title, link, snippet FROM final_results WHERE run_key = ? ORDER BY rank",
            [run_key],
        ).fetchall()

        # Close the orchestrator connection before parallel workers open their
        # own connections — avoids holding a write lock across the pool wait.
        connection.close()
        connection = None

        facet_jobs: list[_RerankImprovementJob | _ResultQualityJob] = []
        for stage_name, cand_rows in stage_payloads:
            facet_jobs.append(
                _RerankImprovementJob(
                    run_key=run_key,
                    stage_name=stage_name,
                    query=query or "",
                    cand_rows=cand_rows,
                    db_path=db_path,
                )
            )
        for rank, title, link, snippet in result_rows:
            facet_jobs.append(
                _ResultQualityJob(
                    run_key=run_key,
                    query=query or "",
                    research_goal=research_goal or "",
                    intent=intent or "unknown",
                    rank=rank,
                    title=title or "",
                    link=link or "",
                    snippet=snippet or "",
                    db_path=db_path,
                )
            )

        if facet_jobs:
            with _JUDGE_SCHEDULE_LOCK:
                if _JUDGE_LIFECYCLE.state == "shutting_down":
                    logger.debug(
                        "Judge executor is shutting down; skipping parallel facets for %s",
                        run_key,
                    )
                    return 0
            pool = _DaemonThreadPoolExecutor(
                max_workers=min(4, len(facet_jobs)),
                thread_name_prefix="judge-facet",
            )
            try:
                try:
                    futures = [pool.submit(_run_parallel_facet, job) for job in facet_jobs]
                except RuntimeError:
                    # CPython atexit set the global _shutdown flag; fall back to inline
                    # execution so partial judgments are still persisted.
                    logger.debug(
                        "Judge facet pool submit blocked (interpreter shutting down); "
                        "running %d facets inline for %s",
                        len(facet_jobs),
                        run_key,
                    )
                    for job in facet_jobs:
                        try:
                            judgments_written += int(_run_parallel_facet(job) or 0)
                        except Exception:
                            logger.exception("inline judge facet failed for run_key=%s", run_key)
                    return judgments_written
                for fut in as_completed(futures):
                    try:
                        judgments_written += int(fut.result() or 0)
                    except RuntimeError:
                        logger.debug(
                            "Judge facet pool shut down mid-run for %s; "
                            "preserving %d partial judgments",
                            run_key,
                            judgments_written,
                        )
                        return judgments_written
                    except Exception:
                        logger.exception("parallel judge facet failed for run_key=%s", run_key)
            finally:
                try:
                    pool.shutdown(wait=False, cancel_futures=True)
                except RuntimeError:
                    pass
        # Re-open connection for failure_cause (serial, needs branch errors).
        connection = _connect(db_path)
        loaded_ok = _ensure_loaded(connection)
        if not loaded_ok:
            logger.warning(
                "FlockMTL reload failed after parallel facets, skipping failure_cause for %s",
                run_key,
            )

        # (f) judge_failure_cause -- 1 call iff status != 'success' OR
        # final_count == 0 (judge_quality). Branch error signals come
        # from `_fetch_branch_errors` (JOIN onto provider_calls,
        # not search_branches -- the latter has no error_type column).
        if loaded_ok and (status != "success" or (final_count or 0) == 0):
            branch_err_rows = _fetch_branch_errors(connection, run_key)
            branch_lines = []
            for role, rc, perr in branch_err_rows:
                line = f"  - {role or 'unknown'}: {rc or 0} results"
                if perr:
                    line += f"  provider_errors={perr}"
                branch_lines.append(line)
            if not branch_lines:
                branch_lines = ["  (no branch rows)"]
            failure_ctx = _ctx(
                ("query", query or ""),
                ("intent", intent or "unknown"),
                ("error_type", error_type or ""),
                ("providers", providers_str),
                ("branch_errors", "\n".join(branch_lines)),
            )
            raw, duration = _run_prompt(
                connection,
                model_name=_JUDGE_MODEL,
                prompt_name="judge_failure_cause",
                context_columns=failure_ctx,
            )

            def _failure_verdict(p: dict) -> str:
                return f"{p.get('root_cause', 'other')} @ {p.get('stage', '?')}"

            _store_judgment_row(
                connection,
                run_key=run_key,
                judgment_kind="failure_cause",
                judgment_target=run_key,
                prompt_name="judge_failure_cause",
                model_name=_JUDGE_MODEL,
                raw=raw,
                duration=duration,
                parsed=_parse_result(raw),
                context_columns=failure_ctx,
                build_verdict=_failure_verdict,
                build_reasoning=lambda p: str(p.get("reasoning") or ""),
            )
            judgments_written += 1

        logger.info(
            "judge_search_run(%s): wrote %d judgments (status=%s, finals=%s)",
            run_key,
            judgments_written,
            status,
            final_count,
        )
        return judgments_written
    except Exception:
        logger.exception("judge_search_run(%s) failed", run_key)
        return 0
    finally:
        if connection is not None:
            connection.close()


def schedule_judge_search_run(run_key: str) -> Future[int]:
    """Fire-and-forget judge for `run_key` on the global thread pool.

    Returns the submitted Future so callers (tests, observability) can
    inspect the result. Production callers ignore the return value --
    judge failures are logged but never break the search pipeline.

    The returned Future resolves to the integer count of judgments
    persisted by `judge_search_run` (0 on failure, which itself logs
    the underlying exception).

    Shutdown resilience
    -------------------
    Two guards protect against the CPython 3.12 shutdown race where
    ``_python_exit`` (atexit) sets the module-level ``_shutdown`` flag
    in ``concurrent.futures.thread``, which blocks ALL
    ``ThreadPoolExecutor.submit()`` calls — including on our
    ``_DaemonThreadPoolExecutor`` that deliberately skips
    ``_threads_queues`` registration:

    1. ``_JUDGE_SCHEDULE_LOCK`` serialises the lifecycle-state check
       with ``shutdown_judge_executor`` so our own shutdown path
       never races with submission.

    2. If ``submit()`` raises ``RuntimeError`` (from CPython's
       module-level ``_shutdown`` flag), the judge runs **inline** on
       the calling thread.  This guarantees the FlockMTL verdicts are
       persisted durably to the DuckDB database even when CPython's
       atexit handler has already started shutting down thread pools.
       The inline call opens its own short-lived DuckDB connection and
       writes independently, so there is no conflict with any
       already-closed caller connection.
    """
    with _JUDGE_SCHEDULE_LOCK:
        if _JUDGE_LIFECYCLE.state == "shutting_down":
            logger.debug("Judge executor is shutting down; skipping judge for %s", run_key)
            f: Future[int] = Future()
            f.set_result(0)
            return f
        executor = _get_judge_executor()

    try:
        future = executor.submit(judge_search_run, run_key)
    except RuntimeError:
        # CPython's _python_exit (atexit) set the module-level _shutdown
        # flag in concurrent.futures.thread, which blocks ALL
        # ThreadPoolExecutor.submit() calls.  Fall back to inline
        # execution so the judge still runs and scores are persisted.
        logger.debug(
            "Judge executor submit blocked (interpreter shutting down); "
            "running judge inline for %s",
            run_key,
        )
        try:
            count = judge_search_run(run_key)
        except Exception:
            logger.exception("Inline judge failed for %s", run_key)
            count = 0
        f = Future()
        f.set_result(count)
        return f

    with _PENDING_JUDGE_FUTURES_LOCK:
        _PENDING_JUDGE_FUTURES.add(future)

    def _discard(f: Future[int]) -> None:
        with _PENDING_JUDGE_FUTURES_LOCK:
            _PENDING_JUDGE_FUTURES.discard(f)

    future.add_done_callback(_discard)
    return future
