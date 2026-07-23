"""Detached background persistence lifecycle for completed searches."""

from __future__ import annotations
import asyncio
import logging

LOGGER = logging.getLogger(__name__)
_OUTCOME_TASKS: set[asyncio.Task[None]] = set()
_EMBEDDING_DIM = 1024


def _validate_embedding(vec, label):
    if len(vec) == _EMBEDDING_DIM:
        return vec
    LOGGER.warning("Embedding %s dim=%d expected %d", label, len(vec), _EMBEDDING_DIM)
    return vec


async def persist_search_outcome(run):
    from ..analytics.async_writes import dispatch_duckdb_write
    from ..analytics.duckdb_store import (
        insert_candidate_embeddings,
        insert_final_results,
        insert_provider_calls,
        insert_query_embeddings,
        insert_rerank_stages,
        insert_search_branches,
        insert_search_candidates,
        insert_search_run,
    )
    from .diagnostics import build_diagnostics

    outcome = run.snapshot()
    total = run.diagnostics.total_latency_ms or 0.0
    try:
        d = build_diagnostics(run, total)
        diag = d.model_dump() if hasattr(d, "model_dump") else {}
    except Exception:
        diag = {}
    r = outcome.response
    rk = outcome.run_key
    dc = run.diagnostics
    en = diag.get("enrichment", {})
    rw = diag.get("rewrite", {})
    mc = dc.merge_counts or {}
    writes = []

    selected: list[str] = []
    if outcome.plan is not None:
        names: set[str] = set()
        for b in outcome.plan.branches:
            names.update(b.provider_names)
        selected = sorted(names)

    writes.append(
        {
            "_w": insert_search_run,
            "run_key": rk,
            "query": outcome.request.query,
            "normalized_query": outcome.plan.normalized_query if outcome.plan else "",
            "research_goal": outcome.request.research_goal,
            "intent": dc.intent or "",
            "understanding_confidence": dc.understanding_confidence,
            "num_results_requested": outcome.request.num_results,
            "rewrite_enabled": outcome.request.rewrite,
            "selected_providers": selected,
            "skipped_providers": [],
            "branch_count": len(outcome.outcomes),
            "provider_count": mc.get("provider_count", 0),
            "merged_count": mc.get("merged_count", 0),
            "reranked_count": mc.get("reranked_count", 0),
            "final_result_count": len(r.results) if r is not None else 0,
            "candidate_count": mc.get("candidate_count", 0),
            "status": outcome.status,
            "error_type": outcome.error_summary,
            "duration_ms": (
                dc.total_latency_ms
                if dc.total_latency_ms is not None
                else sum(outcome.timings.values())
            ),
            "reranker_provider": outcome.rerank_metadata.get("reranker_provider"),
            "reranker_model": outcome.rerank_metadata.get("reranker_model"),
            "rake_terms": en.get("rake_terms", []),
            "brave_autosuggest": en.get("brave_autosuggest", []),
            "brave_spellcheck": en.get("brave_spellcheck"),
            "rewrite_prompt": rw.get("prompt"),
            "rewrite_model": rw.get("model"),
            "rewrite_input_tokens": rw.get("input_tokens"),
            "rewrite_output_tokens": rw.get("output_tokens"),
            "rewrite_latency_ms": rw.get("latency_ms"),
            "rewrite_error": rw.get("error"),
            "rewritten_branch_queries": (
                list(outcome.plan.rewrite_queries)
                if outcome.plan and outcome.plan.rewrite_queries
                else None
            ),
            "payload_json": {
                "tool_call_id": outcome.tool_call_id,
                "session_id": outcome.session_id,
                "phase_timings": dc.phase_timings,
                "funnel_counts": outcome.rerank_metadata.get("funnel_counts") or {},
            },
        }
    )
    for i, ob in enumerate(outcome.outcomes):
        b = ob.branch
        writes.append(
            {
                "_w": insert_search_branches,
                "run_key": rk,
                "branch_index": i,
                "branch_role": b.role.value,
                "branch_query": b.query,
                "branch_why": b.why,
                "support_terms": list(b.support_terms),
                "max_results": b.max_results,
                "assigned_providers": list(b.provider_names),
                "attempted_providers": list(ob.attempted_provider_names),
                "skipped_providers": list(ob.skipped_provider_names),
                "results_count": len(ob.results),
                "latency_ms": ob.elapsed_seconds * 1000.0,
                "payload_json": {},
            }
        )
    for br in dc.branch_results:
        for c in br.get("provider_calls", []):
            writes.append(
                {
                    "_w": insert_provider_calls,
                    "run_key": rk,
                    "branch_index": br.get("branch_index"),
                    "branch_role": c.get("branch_role"),
                    "provider": c.get("provider"),
                    "branch_query": br.get("branch_query"),
                    "status": c.get("status", "unknown"),
                    "num_results_requested": br.get("max_results"),
                    "num_results_returned": c.get("num_results_returned", 0),
                    "latency_ms": c.get("latency_ms"),
                    "error_type": c.get("error_type"),
                    "error_message": c.get("error_message"),
                    "candidate_urls": c.get("candidate_urls", []),
                    "payload_json": {},
                }
            )
    for rank, res in enumerate(dc.merged_candidates, start=1):
        writes.append(
            {
                "_w": insert_search_candidates,
                "run_key": rk,
                "link": res.link,
                "title": res.title,
                "snippet": res.snippet,
                "domain": res.domain or "",
                "rrf_score": res.score or 0.0,
                "provider_count": res.provider_count or 0,
                "providers": list(res.providers or []),
                "overlap_flag": (res.provider_count or 0) > 1,
                "payload_json": {"rank": rank},
            }
        )
    if r is not None:
        for rank, res in enumerate(r.results, start=1):
            writes.append(
                {
                    "_w": insert_final_results,
                    "run_key": rk,
                    "rank": rank,
                    "title": res.title,
                    "link": res.link,
                    "snippet": res.snippet,
                    "domain": res.domain or "",
                    "final_score": res.score,
                    "providers": list(res.providers or []),
                    "provider_count": res.provider_count or 0,
                    "entities_count": 0,
                    "candidate_id": None,
                    "canonical_result_id": None,
                    "payload_json": {},
                }
            )
    if dc.query_embedding is not None:
        v = _validate_embedding(dc.query_embedding, "query")
        writes.append(
            {
                "_w": insert_query_embeddings,
                "run_key": rk,
                "embedding": v,
                "model_id": "intfloat/multilingual-e5-large-instruct",
                "payload_json": {"dim": len(v)},
            }
        )
    for c in dc.candidate_embeddings:
        v = _validate_embedding(c.get("dense", []), "cand:" + c.get("url", ""))
        t = (c.get("text") or "").split("\n", 1)[0] if c.get("text") else ""
        writes.append(
            {
                "_w": insert_candidate_embeddings,
                "run_key": rk,
                "link": c.get("url", ""),
                "title": t,
                "embedding": v,
                "model_id": "intfloat/multilingual-e5-large-instruct",
                "payload_json": {"dim": len(v), "text_preview": (c.get("text") or "")[:200]},
            }
        )
    for s in dc.rerank_stage_summaries:
        writes.append(
            {
                "_w": insert_rerank_stages,
                "run_key": rk,
                "stage": s.get("stage"),
                "provider": s.get("provider"),
                "model": s.get("model"),
                "input_count": s.get("input_count"),
                "output_count": s.get("output_count"),
                "duration_ms": s.get("duration_ms"),
                "max_score": s.get("max_score"),
                "avg_score": s.get("avg_score"),
                "score_threshold": s.get("score_threshold"),
                "alpha_blend": s.get("alpha_blend"),
                "input_tokens": s.get("input_tokens"),
                "output_tokens": s.get("output_tokens"),
                "status": s.get("status"),
                "error_type": s.get("error_type"),
                "instruction_present": s.get("instruction_present"),
                "instruction_length": s.get("instruction_length"),
                "query_type_hint": s.get("query_type_hint"),
                "entity_overlap_enabled": s.get("entity_overlap_enabled"),
                "payload_json": s.get("payload_json") or {},
            }
        )

    # The primary `insert_search_run` MUST succeed for the judge to have
    # any rows to query — if it fails, abort the writer so the
    # done-callback sees the exception and skips scheduling. Secondary
    # analytics rows (branches, candidates, etc.) remain best-effort:
    # their failure doesn't change the outcome of judging this run.
    primary = writes[0] if writes else None
    rest = writes[1:]

    def _write():
        if primary is not None:
            primary_fn = primary.pop("_w")
            primary_fn(**primary)  # let exceptions propagate
        for w in rest:
            fn = w.pop("_w")
            try:
                fn(**w)
            except Exception as e:
                LOGGER.debug("persist %s failed: %s", fn.__name__, e)
        try:
            from ..analytics.quality_metrics import compute_search_quality

            compute_search_quality(rk)
        except Exception as e:
            LOGGER.debug("persist search quality failed: %s", e)

    future = dispatch_duckdb_write("search.outcome." + rk, _write)
    if future is not None:
        # Judge scheduling runs in a done-callback on the write future,
        # NOT inside _write() on the DuckDB write executor thread.  This
        # avoids a shutdown race: CPython 3.12's _python_exit (atexit)
        # sets a module-level _shutdown flag in concurrent.futures.thread
        # that blocks ALL ThreadPoolExecutor.submit() calls regardless of
        # whether our executor skipped _threads_queues registration.  The
        # done-callback fires on the DuckDB write executor thread when
        # set_result() is called, but schedule_judge_search_run catches
        # the resulting RuntimeError and falls back to inline execution
        # so judge scores are always persisted durably.

        def _on_write_done(f):
            """Schedule FlockMTL judge after DuckDB write confirms the
            search_runs row is persisted.  Silently skip on write failure
            (primary insert didn't succeed — nothing to evaluate)."""
            try:
                f.result()  # Re-raise if primary insert failed
            except Exception:
                return  # Nothing to judge
            try:
                from ..analytics.judges import schedule_judge_search_run

                schedule_judge_search_run(rk)
            except Exception:
                LOGGER.exception("Failed to schedule judge for %s", rk)

        future.add_done_callback(_on_write_done)
    return future


def submit_search_outcome(run):
    # Judge scheduling happens via the write-done callback inside
    # persist_search_outcome, not here. This keeps the search
    # response latency independent of the judge's eventual write.
    task = asyncio.create_task(persist_search_outcome(run), name="search.outcome." + run.run_key)
    _OUTCOME_TASKS.add(task)
    task.add_done_callback(_OUTCOME_TASKS.discard)
    return task


async def drain_search_outcomes(timeout_seconds=10.0):
    if not _OUTCOME_TASKS:
        return
    tasks = tuple(_OUTCOME_TASKS)
    try:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout_seconds)
    except TimeoutError:
        unfinished = [t for t in tasks if not t.done()]
        LOGGER.error("Timed out draining: %s", [t.get_name() for t in unfinished])
        for t in unfinished:
            t.cancel()
        await asyncio.gather(*unfinished, return_exceptions=True)
