"""Read-only replay of persisted graph-expansion decisions."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
import math
from typing import Any

import duckdb

from ..search.graph_expansion import GraphExpansionDecision, expand_seed_queries
from ..settings import settings
from .writers.connection import _db_path


@dataclass(frozen=True, slots=True)
class GraphExpansionReplayRow:
    run_key: str
    normalized_query: str
    base_seed_queries: tuple[str, ...]
    decision: GraphExpansionDecision


@dataclass(frozen=True, slots=True)
class GraphExpansionReplayReport:
    rows: tuple[GraphExpansionReplayRow, ...]
    fallback_seed_count: int
    outcome_metrics: dict[str, dict[str, object]] = field(default_factory=dict)

    @property
    def decision_counts(self) -> dict[str, int]:
        return dict(Counter(row.decision.status for row in self.rows))

    def _metrics(self) -> dict[str, object]:
        total = len(self.rows)
        decision_counts = self.decision_counts
        query_frequency = Counter(row.normalized_query for row in self.rows)
        support_counts = Counter(
            support
            for row in self.rows
            for _, support in row.decision.candidate_support_counts
        )
        prompt_delta_chars = sum(
            len(" ".join(row.decision.effective_seed_queries))
            - len(" ".join(row.base_seed_queries))
            for row in self.rows
        )
        return {
            "branch_cardinality": {"6": total},
            "candidate_support_distribution": dict(sorted(support_counts.items())),
            "eligible_query_coverage": (
                sum(row.decision.generation_id is not None for row in self.rows) / total
                if total
                else 0.0
            ),
            "effective_seed_distribution": dict(
                sorted(Counter(len(row.decision.effective_seed_queries) for row in self.rows).items())
            ),
            "head_tail_query_split": {
                "head": sum(query_frequency[row.normalized_query] > 1 for row in self.rows),
                "tail": sum(query_frequency[row.normalized_query] == 1 for row in self.rows),
            },
            "no_match_stale_error_rate": (
                sum(
                    decision_counts.get(status, 0)
                    for status in ("no_match", "stale", "error", "unavailable")
                )
                / total
                if total
                else 0.0
            ),
            "prompt_size_delta_chars": prompt_delta_chars,
            "related_query_coverage": (
                sum(bool(row.decision.related_queries) for row in self.rows) / total if total else 0.0
            ),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "runs_replayed": len(self.rows),
            "fallback_seed_count": self.fallback_seed_count,
            "decision_counts": self.decision_counts,
            "outcome_metrics": self.outcome_metrics,
            "metrics": self._metrics(),
            "rows": [
                {
                    "run_key": row.run_key,
                    "normalized_query": row.normalized_query,
                    "base_seed_queries": list(row.base_seed_queries),
                    "status": row.decision.status,
                    "generation_id": row.decision.generation_id,
                    "source_fingerprint": row.decision.source_fingerprint,
                    "matched_query": row.decision.matched_query,
                    "candidate_support_counts": dict(row.decision.candidate_support_counts),
                    "effective_seed_queries": list(row.decision.effective_seed_queries),
                    "related_queries": list(row.decision.related_queries),
                    "dropped_candidates": [
                        {"query": query, "reason": reason}
                        for query, reason in row.decision.dropped_candidates
                    ],
                    "error_type": row.decision.error_type,
                }
                for row in self.rows
            ],
        }


def _stored_seed_queries(
    payload_json: object, normalized_query: str
) -> tuple[tuple[str, ...], bool]:
    """Recover persisted plan seeds or apply plan_search's current fallback."""
    payload: Any = payload_json
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = None

    if isinstance(payload, Mapping):
        raw_seeds = payload.get("seed_queries")
        if isinstance(raw_seeds, list) and all(
            isinstance(seed, str) and seed.strip() for seed in raw_seeds
        ):
            seeds = tuple(raw_seeds)
            if seeds:
                return seeds, False

    return (normalized_query,), True



def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _ndcg_and_mrr(labels: list[tuple[int, float]]) -> tuple[float | None, float | None]:
    top_labels = [(position, label) for position, label in labels if 0 <= position < 10]
    if not top_labels:
        return None, None
    dcg = sum((2.0**label - 1.0) / math.log2(position + 2) for position, label in top_labels)
    ideal_labels = sorted((label for _, label in top_labels), reverse=True)
    idcg = sum(
        (2.0**label - 1.0) / math.log2(position + 2)
        for position, label in enumerate(ideal_labels[:10])
    )
    ndcg = dcg / idcg if idcg > 0.0 else None
    mrr = next(
        (1.0 / (position + 1) for position, label in sorted(top_labels) if label > 0.0),
        None,
    )
    return ndcg, mrr


def _summarize_outcomes(
    run_rows: Sequence[
        tuple[str, str | None, int | None, str | None, float | None, float | None]
    ],
    labels_by_run: dict[str, list[tuple[int, float]]],
    domains_by_run: dict[str, set[str]],
    costs_by_run: dict[str, float],
) -> dict[str, dict[str, object]]:
    groups: dict[str, list[tuple[str, str | None, int | None, str | None, float | None, float | None]]] = {
        "control": [],
        "treatment": [],
    }
    for row in run_rows:
        group = "treatment" if row[1] == "applied" else "control"
        groups[group].append(row)

    summaries: dict[str, dict[str, object]] = {}
    for group, rows in groups.items():
        quality_values: list[float] = []
        ndcg_values: list[float] = []
        mrr_values: list[float] = []
        domain_counts: list[float] = []
        provider_counts: list[float] = []
        latencies: list[float] = []
        costs: list[float] = []
        zero_results = 0
        rewrite_failures = 0
        for run_key, _, final_count, rewrite_error, provider_count, duration_ms in rows:
            labels = labels_by_run.get(run_key, [])
            if labels:
                quality_values.append(sum(label for _, label in labels) / len(labels))
                ndcg, mrr = _ndcg_and_mrr(labels)
                if ndcg is not None:
                    ndcg_values.append(ndcg)
                if mrr is not None:
                    mrr_values.append(mrr)
            domain_counts.append(float(len(domains_by_run.get(run_key, set()))))
            if final_count == 0:
                zero_results += 1
            if rewrite_error and rewrite_error.strip():
                rewrite_failures += 1
            if provider_count is not None:
                provider_counts.append(float(provider_count))
            if duration_ms is not None and math.isfinite(duration_ms):
                latencies.append(duration_ms)
            if run_key in costs_by_run and math.isfinite(costs_by_run[run_key]):
                costs.append(costs_by_run[run_key])
        run_count = len(rows)
        summaries[group] = {
            "run_count": run_count,
            "judged_run_count": len(quality_values),
            "mean_result_quality": _mean(quality_values),
            "ndcg_at_10": _mean(ndcg_values),
            "mrr_at_10": _mean(mrr_values),
            "mean_top10_unique_domain_count": _mean(domain_counts),
            "zero_result_rate": zero_results / run_count if run_count else 0.0,
            "rewrite_failure_rate": rewrite_failures / run_count if run_count else 0.0,
            "mean_provider_count": _mean(provider_counts),
            "mean_cost_usd": _mean(costs),
            "mean_planner_latency_ms": _mean(latencies),
        }
    return summaries

def replay_graph_expansion(
    *,
    db_path: str | None = None,
    sqlite_path: str | None = None,
) -> GraphExpansionReplayReport:
    """Replay graph decisions and summarize persisted control/treatment outcomes."""
    path = _db_path(db_path)
    if not path.exists():
        return GraphExpansionReplayReport(rows=(), fallback_seed_count=0)

    runs: list[tuple[str, str, object, str | None, int | None, str | None, float | None, float | None]] = []
    labels_by_run: dict[str, list[tuple[int, float]]] = {}
    domains_by_run: dict[str, set[str]] = {}
    costs_by_run: dict[str, float] = {}
    con = duckdb.connect(str(path), read_only=True)
    try:
        table_names = {
            row[0]
            for row in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }
        if "search_runs" not in table_names:
            return GraphExpansionReplayReport(rows=(), fallback_seed_count=0)

        runs = con.execute(
            """
            SELECT
                run_key,
                normalized_query,
                payload_json,
                json_extract_string(payload_json, '$.graph_expansion.status'),
                final_result_count,
                rewrite_error,
                provider_count,
                duration_ms
            FROM search_runs
            WHERE normalized_query IS NOT NULL
              AND trim(normalized_query) != ''
            ORDER BY recorded_at ASC, run_key ASC
            """
        ).fetchall()

        if "result_labels" in table_names:
            for run_key, position, label in con.execute(
                """
                SELECT run_key, position, label
                FROM result_labels
                WHERE source = 'llm_judge'
                  AND position IS NOT NULL
                  AND label IS NOT NULL
                """
            ).fetchall():
                try:
                    position_value = int(position)
                    label_value = float(label)
                except (TypeError, ValueError):
                    continue
                if position_value >= 0 and math.isfinite(label_value):
                    labels_by_run.setdefault(run_key, []).append(
                        (position_value, label_value)
                    )

        if "final_results" in table_names:
            for run_key, rank, domain in con.execute(
                """
                SELECT run_key, rank, domain
                FROM final_results
                WHERE rank BETWEEN 1 AND 10
                  AND domain IS NOT NULL
                  AND trim(domain) != ''
                """
            ).fetchall():
                if rank is not None and domain:
                    domains_by_run.setdefault(run_key, set()).add(str(domain).casefold())

        if "llm_call_log" in table_names:
            for run_key, cost in con.execute(
                """
                SELECT run_key, SUM(cost_usd)
                FROM llm_call_log
                WHERE cost_usd IS NOT NULL
                GROUP BY run_key
                """
            ).fetchall():
                if cost is not None:
                    costs_by_run[run_key] = float(cost)
    finally:
        con.close()

    replay_rows: list[GraphExpansionReplayRow] = []
    fallback_seed_count = 0
    for run_key, normalized_query, payload_json, *_ in runs:
        base_seed_queries, used_fallback = _stored_seed_queries(payload_json, normalized_query)
        fallback_seed_count += int(used_fallback)
        decision = expand_seed_queries(
            normalized_query=normalized_query,
            base_seed_queries=base_seed_queries,
            enabled=True,
            max_related_queries=settings.graph_expansion_max_related_queries,
            max_age_seconds=settings.graph_expansion_max_age_seconds,
            artifact_path=sqlite_path,
        )
        replay_rows.append(
            GraphExpansionReplayRow(
                run_key=run_key,
                normalized_query=normalized_query,
                base_seed_queries=base_seed_queries,
                decision=decision,
            )
        )

    outcome_rows = [
        (run_key, graph_status, final_count, rewrite_error, provider_count, duration_ms)
        for run_key, _, _, graph_status, final_count, rewrite_error, provider_count, duration_ms in runs
        if graph_status is not None
    ]
    return GraphExpansionReplayReport(
        rows=tuple(replay_rows),
        fallback_seed_count=fallback_seed_count,
        outcome_metrics=_summarize_outcomes(
            outcome_rows, labels_by_run, domains_by_run, costs_by_run
        ),
    )
