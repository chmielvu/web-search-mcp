"""Run-digest construction + FlockMTL context_columns helper.

Owns the structural run digest (``_build_run_digest``) that the
``judge_run_overview`` facet judges against, the per-branch error
joiner (``_fetch_branch_errors``), the overview-reasoning formatter
(``_format_overview_reasoning``), the small ``_ctx`` helper that builds
FlockMTL ``context_columns`` rows, and the three reranker-blindness
constants used throughout the digest.
"""

from __future__ import annotations

import logging

import duckdb

logger = logging.getLogger(__name__)

# Hard ceiling for any single rerank probe the digest reports: we
# top-10 to keep the digest ≤ ~2k tokens.
_DIGEST_TOP_N_FINAL = 10

# Score names banned from `judge_run_overview`, `judge_rerank_improvement`,
# and `judge_result_quality` contexts (G-Eval self-enhancement bias).
_BANNED_RERANK_SCORES = (
    "final_score",
    "rankllm_score",
    "cross_encoder_score",
    "retrieval_rrf_score",
    "bi_encoder_score",
    "recency_score",
    "diversity_penalty",
)

_REWRITE_STRATEGIES = (
    "augmented free-web query",
    "SERP1 keyword query",
    "SERP2 keyword query",
    "semantic Tavily query",
    "semantic Exa query",
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
    reranker scores (`final_score`, `rankllm_score`, `cross_encoder_score`,
    `retrieval_rrf_score`, `bi_encoder_score`, `recency_score`,
    `diversity_penalty`) -- the overview is a summary,
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
        for i, r in enumerate(rewrites[:5]):
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

    # Rerank stage structural summary -- stage name and counts.
    # No raw scores (the digest is reranker-blind).
    stages = connection.execute(
        "SELECT stage, provider, model, input_count, output_count, status, "
        "error_type "
        "FROM rerank_stages WHERE run_key = ? ORDER BY recorded_at",
        [run_key],
    ).fetchall()
    if stages:
        parts.append("rerank stages:")
        for stage, prov, model, inp, outp, sst, serr in stages:
            line = (
                f"  - {stage or '?'}: in={inp or 0} out={outp or 0} "
                f"st={sst or '?'} prov={prov or '?'} m={model or '?'}"
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
