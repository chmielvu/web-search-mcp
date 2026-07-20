"""FlockMTL-backed semantic judgment orchestrator.

Runs FlockMTL prompts on each completed search run and persists
verdicts to the `llm_judgments` table. This is the "judge every search
automatically" path the user asked for — no per-row LLM calls from
views, no surprise API costs on dashboard refresh.

Per-search judge pass fires six facet-decomposed judgments (all on
`judge_quality` / 120B; calibration A/B uses `judge_fast` separately):

  a. `judge_run_overview`     -- 1 call/run; holistic good/mixed/bad
                                + analysis + recommendations
  b. `judge_intent_coherence` -- 1 call/run; intent matches query+goal
  c. `judge_rewrite_coverage` -- 1 call/run; if rewrite_enabled &
                                non-empty rewrites; counts distinct facets
  d. `judge_rerank_improvement` -- 1 call per rerank_stages row;
                                positional only (no reranker scores)
  e. `judge_result_quality`   -- 1 call per final_results row (≤ 15/run);
                                snippet-only, NO reranker scores
  f. `judge_failure_cause`    -- 1 call/run if status != 'success' OR
                                final_count == 0; few-shot triage

Each call to `judge_search_run(run_key)` opens its own short-lived
DuckDB connection (LOADs FlockMTL on it, registers the
`__default_openai` secret), runs the prompts, INSERTs the verdicts,
and closes. Failures are caught and persisted as `status='error'` rows
so the orchestrator never crashes the calling search pipeline.

Cost guard: skips entirely if `settings.flockmtl_enabled` is False.

For search-pipeline callers, use `schedule_judge_search_run(run_key)` --
fire-and-forget on a thread pool, never blocks the search response.
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import duckdb

from ..settings import settings
from .writers.connection import _db_path, ensure_flockmtl_loaded

logger = logging.getLogger(__name__)

# Thread pool for fire-and-forget judge runs from the search pipeline.
# Sized to absorb bursts without blocking the search event loop.
_JUDGE_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="judge")

# Default rubric version stamped on every judgment row. A future prompt
# bump would set this to 'v2' (and rename the FlockMTL prompt) so v1 and
# v2 coexist in the catalog and trend comparisons stay filterable.
_DEFAULT_RUBRIC_VERSION = "v1"

# Hard ceiling for any single rerank probe the digest reports: we
# top-10 to keep the digest ≤ ~2k tokens.
_DIGEST_TOP_N_FINAL = 10

# Score names banned from `judge_run_overview`, `judge_rerank_improvement`,
# and `judge_result_quality` contexts (G-Eval self-enhancement bias).
_BANNED_RERANK_SCORES = (
    "final_score",
    "llm_raw_score",
    "cross_encoder_raw",
    "fused_score",
    "hybrid_rrf_score",
)

# Module-level model selector for `judge_search_run`. Production callers
# leave this at the default `"judge_quality"` (the 120B Mistral model).
# The calibration harness (`analytics/judge_calibration.py`) rebinds it
# to `"judge_fast"` (the 3B model) to produce A/B rows alongside the
# production 120B rows, then restores it. Defined as a private mutable
# default — calibration is the only intentional mutator.
_JUDGE_MODEL = "judge_quality"


def _connect(db_path: str | None = None) -> duckdb.DuckDBPyConnection:
    """Open a short-lived analytics DuckDB connection (read-write)."""
    return duckdb.connect(str(_db_path(db_path)), read_only=False)


def _ensure_loaded(connection: duckdb.DuckDBPyConnection) -> bool:
    """Ensure the FlockMTL extension + secret are usable on this connection.

    The `__default_openai` secret is connection-local (CREATE SECRET
    without PERSISTENT is per-connection), so we re-register on every
    connection that runs `llm_complete`. The CREATE SECRET DDL must
    serialize with the user-DB writer: a concurrent catalog write
    (e.g. `compute_search_quality` still committing on the write
    executor) raises TransactionContextError. We serialize under the
    analytics `_LOCK` and retry briefly on catalog-write conflict.
    """
    if not ensure_flockmtl_loaded(connection):
        return False
    try:
        # Lazy import to avoid circular dependency at module import time.
        from .writers.connection import _LOCK, _ensure_flockmtl_secret

        for _attempt in range(5):
            with _LOCK:
                try:
                    _ensure_flockmtl_secret(connection)
                    return True
                except duckdb.TransactionException as exc:
                    # Catalog write-write conflict with the writer
                    # thread; brief retry after the writer commits.
                    logger.debug("flockmtl secret catalog conflict, retrying: %s", exc)
                    time.sleep(0.05)
        logger.warning("flockmtl secret registration exhausted retries")
        return False
    except Exception:
        logger.exception("flockmtl secret registration failed")
        return False


def _run_prompt(
    connection: duckdb.DuckDBPyConnection,
    *,
    model_name: str,
    prompt_name: str,
    context_columns: list[dict[str, object]],
) -> tuple[str | None, float]:
    """Call llm_complete and return (verdict_or_none, duration_seconds)."""
    started = time.perf_counter()
    try:
        row = connection.execute(
            "SELECT llm_complete(?, ?)",
            [
                {"model_name": model_name},
                {
                    "prompt_name": prompt_name,
                    "context_columns": context_columns,
                },
            ],
        ).fetchone()
        duration = time.perf_counter() - started
        return (row[0] if row else None, duration)
    except Exception as exc:
        duration = time.perf_counter() - started
        logger.warning(
            "llm_complete failed for model=%s prompt=%s: %s",
            model_name,
            prompt_name,
            exc,
        )
        return (None, duration)


def _parse_result(raw: str | None) -> dict | None:
    """Extract the JSON after the `[RESULT]` token. Return None on any failure.

    The Prometheus scaffold in each prompt asks the model to emit
    "Feedback: <reasoning> [RESULT] <json>". FlockMTL's `llm_complete`
    returns that as one raw text string; we parse the JSON tail here.

    Returns None on: missing raw, missing `[RESULT]` token, malformed
    JSON. Callers must treat None as a parse failure (store a
    `status='error'` row, do NOT crash).
    """
    if not raw:
        return None
    marker = raw.find("[RESULT]")
    if marker < 0:
        return None
    tail = raw[marker + len("[RESULT]") :].strip()
    # Try full JSON first, then first {...} block (covers cases where
    # the model emits trailing commentary after the JSON).
    try:
        return json.loads(tail)
    except Exception:
        m = re.search(r"\{.*\}", tail, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None


def _insert_judgment(
    connection: duckdb.DuckDBPyConnection,
    *,
    run_key: str,
    judgment_kind: str,
    judgment_target: str | None,
    prompt_name: str,
    model_name: str,
    verdict: str | None,
    duration_ms: float,
    status: str,
    facet: str | None = None,
    reasoning: str | None = None,
    rubric_version: str = _DEFAULT_RUBRIC_VERSION,
    confidence: int | None = None,
    context_shown: str | None = None,
    error_message: str | None = None,
) -> None:
    """Persist one judgment row to `llm_judgments` (extended schema)."""
    connection.execute(
        """
        INSERT INTO llm_judgments (
            run_key, judgment_kind, judgment_target, prompt_name, model_name,
            verdict, duration_ms, status, error_message, payload_json,
            facet, reasoning, rubric_version, confidence, context_shown
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            run_key,
            judgment_kind,
            judgment_target,
            prompt_name,
            model_name,
            verdict if verdict is not None else "",
            round(duration_ms * 1000.0, 2),
            status,
            error_message,
            None,
            facet,
            reasoning if reasoning is not None else "",
            rubric_version,
            confidence,
            context_shown,
        ],
    )


def _store_judgment_row(
    connection: duckdb.DuckDBPyConnection,
    *,
    run_key: str,
    judgment_kind: str,
    judgment_target: str | None,
    prompt_name: str,
    model_name: str,
    raw: str | None,
    duration: float,
    parsed: dict | None,
    context_columns: list[dict[str, object]],
    build_verdict,
    build_reasoning,
) -> None:
    """Store one judgment row (success inserts parsed values; error persists raw truncated text).

    `parsed is None` (parse failure or missing `[RESULT]`) -> status='error',
    raw text truncated to 200 chars into the `verdict` column, empty
    `reasoning`. Errors are auditable per facet.
    """
    context_shown = json.dumps(
        [{"name": c.get("name"), "data": str(c.get("data"))[:200]} for c in context_columns]
    )
    if parsed is not None:
        try:
            verdict_str = build_verdict(parsed) or ""
            reasoning_str = build_reasoning(parsed) or ""
        except Exception as exc:
            logger.warning(
                "build_verdict/build_reasoning raised for kind=%s target=%s: %s",
                judgment_kind,
                judgment_target,
                exc,
            )
            verdict_str = (raw or "")[:200]
            reasoning_str = ""
            confidence_int: int | None = None
            status = "error"
            error_message = f"build raised: {exc}"
        else:
            conf = parsed.get("confidence")
            confidence_int = conf if isinstance(conf, int) else None
            status = "success"
            error_message = None
    else:
        verdict_str = (raw or "")[:200]
        reasoning_str = ""
        confidence_int = None
        status = "error"
        error_message = "no [RESULT] parse" if raw else "no llm output"
    _insert_judgment(
        connection,
        run_key=run_key,
        judgment_kind=judgment_kind,
        judgment_target=judgment_target,
        prompt_name=prompt_name,
        model_name=model_name,
        verdict=verdict_str,
        duration_ms=duration,
        status=status,
        facet=judgment_kind,
        reasoning=reasoning_str,
        rubric_version=_DEFAULT_RUBRIC_VERSION,
        confidence=confidence_int,
        context_shown=context_shown,
        error_message=error_message,
    )


_REWRITE_STRATEGIES = (
    "exact-phrase or site-scoped keyword",
    "keyword with exclusions or required terms",
    "broad operator-based keyword",
    "natural-language neural / semantic",
)


def _ctx(*pairs: tuple[str, object]) -> list[dict[str, object]]:
    """Build FlockMTL `context_columns` entries with required `name` + `data` keys.

    FlockMTL's input parser restricts context_columns keys to
    `name, data, type, detail, transcription_model` (verified against
    the flock source -- see `src/functions/input_parser.cpp`). The
    `name` matches `{{name}}` placeholders in the prompt template;
    `data` carries the value.
    """
    return [{"name": name, "data": data} for name, data in pairs]


def _fetch_branch_errors(
    connection: duckdb.DuckDBPyConnection,
    run_key: str,
) -> list[tuple[str | None, int | None, str | None]]:
    """Fetch per-branch error signals via a JOIN onto provider_calls.

    `search_branches` has NO `error_type` column; the canonical
    per-branch error signal lives on `provider_calls.error_type`,
    keyed by `branch_index`. The SELECT whitelists three columns
    from each table so a future schema addition can't silently leak
    banned fields. Returns list of (branch_role, results_count,
    comma-joined provider error_types or None).
    """
    rows = connection.execute(
        """
        SELECT sb.branch_role, sb.results_count,
               (SELECT string_agg(pc.error_type, '/') FILTER (WHERE pc.error_type IS NOT NULL)
                FROM provider_calls pc
                WHERE pc.run_key = sb.run_key AND pc.branch_index = sb.branch_index
               ) AS provider_err_str
        FROM search_branches sb
        WHERE sb.run_key = ?
        ORDER BY sb.branch_index
        """,
        [run_key],
    ).fetchall()
    return rows


def _build_run_digest(
    connection: duckdb.DuckDBPyConnection,
    run_key: str,
) -> str:
    """Build a compact one-string digest of the entire run for judge_run_overview.

    Includes STRUCTURAL facts only: ranks, titles, links, counts,
    branches, rerank stage names + counts. Does NOT include raw
    reranker scores (`final_score`, `llm_raw_score`, `cross_encoder_raw`,
    `fused_score`, `hybrid_rrf_score`) -- the overview is a summary,
    not a rubber-stamp of the reranker. Empty fields render as empty
    strings; no exceptions raised on missing rows.
    """
    run_row = connection.execute(
        "SELECT query, intent, research_goal, understanding_confidence, "
        "rewritten_branch_queries, selected_providers, branch_count, "
        "merged_count, reranked_count, final_result_count, candidate_count, "
        "rewrite_enabled, error_type, status "
        "FROM search_runs WHERE run_key = ?",
        [run_key],
    ).fetchone()
    if not run_row:
        return ""
    (
        q,
        intent,
        research_goal,
        uc,
        rewritten,
        providers,
        branch_count,
        merged,
        reranked,
        final_count,
        cand_count,
        rewrite_enabled,
        err_type,
        st,
    ) = run_row
    rewrites = [str(s) for s in (rewritten or []) if s] or []
    providers_str = ", ".join(providers) if providers else "none"
    parts: list[str] = []
    parts.append(f"query: {q or ''}")
    parts.append(
        f"intent: {intent or 'unknown'} (conf={uc})  "
        f"goal: {research_goal or ''}  status: {st or '?'}  "
        f"err_type: {err_type or ''}  rewrite_enabled: {bool(rewrite_enabled)}"
    )
    if rewrites:
        parts.append("rewrites:")
        for i, r in enumerate(rewrites[:4]):
            strat = _REWRITE_STRATEGIES[i] if i < len(_REWRITE_STRATEGIES) else "unknown"
            parts.append(f"  {i + 1}. {r}  [{strat}]")
    else:
        parts.append("rewrites: (none -- planner skipped or errored)")

    branches = _fetch_branch_errors(connection, run_key)
    branch_count_actual = branch_count if branch_count is not None else len(branches)
    parts.append(f"branches: {branch_count_actual}  providers: {providers_str}")
    for role, rc, perr in branches:
        line = f"  - {role or 'unknown'}: {rc or 0} results"
        if perr:
            line += f"  provider_errors={perr}"
        parts.append(line)

    parts.append(
        f"candidates: merged={merged or 0}  reranked={reranked or 0}  "
        f"final={final_count or 0}  raw={cand_count or 0}"
    )

    # Reank stage structural summary -- stage name, counts, threshold.
    # No raw scores (the digest is reranker-blind).
    stages = connection.execute(
        "SELECT stage, provider, model, input_count, output_count, status, "
        "error_type, score_threshold "
        "FROM rerank_stages WHERE run_key = ? ORDER BY recorded_at",
        [run_key],
    ).fetchall()
    if stages:
        parts.append("rerank stages:")
        for stage, prov, model, inp, outp, sst, serr, thr in stages:
            line = (
                f"  - {stage or '?'}: in={inp or 0} out={outp or 0} "
                f"thr={thr} st={sst or '?'} prov={prov or '?'} m={model or '?'}"
            )
            if serr:
                line += f" err={serr}"
            parts.append(line)
    else:
        parts.append("rerank stages: (none recorded)")

    # Top N final results -- rank, title, link only (no final_score).
    finals = connection.execute(
        "SELECT rank, title, link FROM final_results WHERE run_key = ? ORDER BY rank LIMIT ?",
        [run_key, _DIGEST_TOP_N_FINAL],
    ).fetchall()
    if finals:
        parts.append(f"final results (top {_DIGEST_TOP_N_FINAL}, rank: title -- link):")
        for rank, title, link in finals:
            parts.append(f"  {rank or '?'}. {title or ''} -- {link or ''}")
    else:
        parts.append("final results: (none)")
    return "\n".join(parts)


def _format_overview_reasoning(parsed: dict) -> str:
    """Format judge_run_overview reasoning: analysis + recommendations block."""
    analysis = str(parsed.get("analysis") or "").strip()
    recs = parsed.get("recommendations") or []
    if not isinstance(recs, list):
        recs = []
    if not analysis and not recs:
        return ""
    lines: list[str] = []
    if analysis:
        lines.append(analysis)
    if recs:
        lines.append("Recommendations:")
        for i, r in enumerate(recs):
            lines.append(f"  {i + 1}. {str(r).strip()}")
    return "\n".join(lines)


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
                "" if understanding_confidence is None else str(understanding_confidence),
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
            for i, variant in enumerate(rewrites[:4]):
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
                return (
                    f"covered={p.get('covered_count', 0)}/4; redundant={bool(p.get('redundant'))}"
                )

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

        # (d) judge_rerank_improvement -- 1 call per rerank_stages row
        # (judge_quality each). BLINDNESS: SELECT whitelist is
        # rank_before, rank_after, survived, link only -- no reranker
        # scores. The ORDER BY rank_after lets the model see the
        # post-rerank top.
        stage_rows = connection.execute(
            "SELECT stage FROM rerank_stages WHERE run_key = ? ORDER BY recorded_at",
            [run_key],
        ).fetchall()
        for (stage_name,) in stage_rows:
            cand_rows = connection.execute(
                "SELECT rank_before, rank_after, survived, link "
                "FROM rerank_candidates WHERE run_key = ? AND stage = ? "
                "ORDER BY rank_after LIMIT 10",
                [run_key, stage_name],
            ).fetchall()
            before_lines = [f"  {rb}. {link}" for rb, _ra, _s, link in cand_rows]
            after_lines = [
                f"  {ra}. {link}  (survived={bool(surv)})" for _rb, ra, surv, link in cand_rows
            ]
            rerank_ctx = _ctx(
                ("query", query or ""),
                ("stage", stage_name or ""),
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
                run_key=run_key,
                judgment_kind="rerank_improvement",
                judgment_target=stage_name or "",
                prompt_name="judge_rerank_improvement",
                model_name=_JUDGE_MODEL,
                raw=raw,
                duration=duration,
                parsed=_parse_result(raw),
                context_columns=rerank_ctx,
                build_verdict=lambda p: str(p.get("verdict") or ""),
                build_reasoning=lambda p: str(p.get("reasoning") or ""),
            )
            judgments_written += 1

        # (e) judge_result_quality -- 1 call per final_results row (≤ 15,
        # judge_quality). BLINDNESS: SELECT whitelist is rank, title, link,
        # snippet only -- NO final_score, NO reranker scores. This is
        # the load-bearing data-plane enforcement of the blindness
        # rule stated in the prompt template; an explicit `SELECT *`
        # here would silently leak banned fields if the schema grows.
        result_rows = connection.execute(
            "SELECT rank, title, link, snippet FROM final_results WHERE run_key = ? ORDER BY rank",
            [run_key],
        ).fetchall()
        for rank, title, link, snippet in result_rows:
            rq_ctx = _ctx(
                ("query", query or ""),
                ("research_goal", research_goal or ""),
                ("intent", intent or "unknown"),
                ("rank", "" if rank is None else str(rank)),
                ("title", title or ""),
                ("snippet", snippet or ""),
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
                run_key=run_key,
                judgment_kind="result_quality",
                judgment_target=link or "",
                prompt_name="judge_result_quality",
                model_name=_JUDGE_MODEL,
                raw=raw,
                duration=duration,
                parsed=_parse_result(raw),
                context_columns=rq_ctx,
                build_verdict=_rq_verdict,
                build_reasoning=lambda p: str(p.get("reasoning") or ""),
            )
            judgments_written += 1

        # (f) judge_failure_cause -- 1 call iff status != 'success' OR
        # final_count == 0 (judge_quality). Branch error signals come
        # from `_fetch_branch_errors` (JOIN onto provider_calls,
        # not search_branches -- the latter has no error_type column).
        if status != "success" or (final_count or 0) == 0:
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
        connection.close()


def schedule_judge_search_run(run_key: str) -> Future[int]:
    """Fire-and-forget judge for `run_key` on the global thread pool.

    Returns the submitted Future so callers (tests, observability) can
    inspect the result. Production callers ignore the return value --
    judge failures are logged but never break the search pipeline.

    The returned Future resolves to the integer count of judgments
    persisted by `judge_search_run` (0 on failure, which itself logs
    the underlying exception).
    """
    try:
        return _JUDGE_EXECUTOR.submit(judge_search_run, run_key)
    except Exception as exc:
        logger.exception("Failed to schedule judge for run_key=%s", run_key)
        # Return a finished, failed Future so callers/tests can still
        # observe the original exception without raising here.
        f: Future[int] = Future()
        f.set_exception(exc)
        return f


__all__ = [
    "judge_search_run",
    "schedule_judge_search_run",
    "_parse_result",
    "_build_run_digest",
    "_store_judgment_row",
    "_BANNED_RERANK_SCORES",
]
