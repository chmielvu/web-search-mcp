"""Direct DuckDB read and in-memory NetworkX graph computation."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import uuid

import duckdb

from .observability_ids import _canonical_result_id
from .graph_store import GraphSnapshot, publish_graph_snapshot
from .quality_metrics import compute_positional_discount
from .writers.connection import _db_path



class GraphBuildError(RuntimeError):
    """Raised when graph construction or convergence fails."""


@dataclass(frozen=True, slots=True)
class GraphBuildConfig:
    source_cutoff: datetime
    label_version: str = "v1"
    lookback_days: int = 60
    scoring_policy_version: str = "judge_gain_confidence_mean_v1"
    canonicalization_version: str = "url_canonical_v1"
    min_shared_documents: int = 2
    max_related_queries: int = 5




def build_graph_snapshot(*, db_path: str | None, config: GraphBuildConfig) -> GraphSnapshot:
    """Read DuckDB facts and compute supervised NetworkX graph features."""
    try:
        import networkx as nx
    except ImportError as exc:
        raise GraphBuildError(f"NetworkX is required for graph building: {exc}") from exc

    cutoff = config.source_cutoff
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise GraphBuildError("source_cutoff must be UTC-aware")
    if cutoff.utcoffset() != timedelta(0):
        raise GraphBuildError("source_cutoff must be expressed in UTC")
    if config.lookback_days <= 0:
        raise GraphBuildError("lookback_days must be positive")
    window_start = cutoff - timedelta(days=config.lookback_days)

    built_at = datetime.now(timezone.utc)
    path = _db_path(db_path)
    if not path.exists():
        raise GraphBuildError(f"Database path does not exist: {path}")

    con = duckdb.connect(str(path), read_only=True)
    try:
        table_names = {
            row[0]
            for row in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }
        if "result_labels" not in table_names or "search_runs" not in table_names:
            raise GraphBuildError("Required tables result_labels or search_runs missing")

        label_rows = con.execute(
            """
            SELECT
                rl.run_key,
                sr.normalized_query,
                rl.canonical_result_id,
                rl.discounted_gain,
                rl.label,
                rl.position,
                epoch(rl.recorded_at) AS recorded_at_epoch,
                rl.payload_json
            FROM result_labels rl
            JOIN search_runs sr ON rl.run_key = sr.run_key
            WHERE rl.source = 'llm_judge'
              AND rl.rubric_version = ?
              AND rl.recorded_at >= to_timestamp(?)
              AND rl.recorded_at <= to_timestamp(?)
              AND sr.normalized_query IS NOT NULL
              AND trim(sr.normalized_query) != ''
              AND rl.canonical_result_id IS NOT NULL
              AND trim(rl.canonical_result_id) != ''
            """,
            [config.label_version, window_start.timestamp(), cutoff.timestamp()],
        ).fetchall()

        exposure_rows = (
            con.execute(
                """
                SELECT
                    sr.normalized_query,
                    fr.link,
                    fr.canonical_result_id,
                    epoch(fr.recorded_at) AS recorded_at_epoch
                FROM final_results fr
                JOIN search_runs sr ON fr.run_key = sr.run_key
                WHERE fr.recorded_at >= to_timestamp(?)
                  AND fr.recorded_at <= to_timestamp(?)
                  AND sr.normalized_query IS NOT NULL
                  AND trim(sr.normalized_query) != ''
                  AND fr.link IS NOT NULL
                  AND trim(fr.link) != ''
                """,
                [window_start.timestamp(), cutoff.timestamp()],
            ).fetchall()
            if "final_results" in table_names
            else []
        )
    finally:
        con.close()

    if not label_rows:
        raise GraphBuildError("No result_labels rows found for the configured source window")

    weights_by_pair_run: dict[tuple[str, str], dict[str, list[float]]] = {}
    retained_observations: list[dict[str, object]] = []
    for run_key, q_norm, cid, disc_gain, label, position, recorded_at_epoch, raw_payload in label_rows:
        query = (q_norm or "").strip()
        canonical_result_id = (cid or "").strip()
        if not run_key or not query or not canonical_result_id:
            continue
        try:
            gain = (
                compute_positional_discount(float(label), int(position))
                if disc_gain is None
                else float(disc_gain)
            )
        except (TypeError, ValueError):
            continue
        confidence_fraction = 1.0
        if raw_payload is not None:
            try:
                parsed_payload = (
                    json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
                )
                if isinstance(parsed_payload, dict):
                    parsed_fields = parsed_payload.get("parsed")
                    fallback_confidence = (
                        parsed_fields.get("confidence", 4) / 4.0
                        if isinstance(parsed_fields, dict)
                        else 1.0
                    )
                    confidence_fraction = float(
                        parsed_payload.get("confidence_fraction", fallback_confidence)
                    )
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        if not all(math.isfinite(value) for value in (gain, confidence_fraction)):
            continue
        contribution = gain * confidence_fraction
        if not math.isfinite(contribution):
            continue
        weights_by_pair_run.setdefault((query, canonical_result_id), {}).setdefault(
            str(run_key), []
        ).append(contribution)
        retained_observations.append(
            {
                "canonical_result_id": canonical_result_id,
                "contribution": contribution,
                "position": position,
                "query": query,
                "recorded_at_epoch": recorded_at_epoch,
                "run_key": str(run_key),
            }
        )

    mean_weights: dict[tuple[str, str], float] = {}
    for pair, weights_by_run in weights_by_pair_run.items():
        run_contributions = [sum(weights) / len(weights) for weights in weights_by_run.values()]
        aggregate = sum(run_contributions) / len(run_contributions)
        if math.isfinite(aggregate) and aggregate > 0.0:
            mean_weights[pair] = aggregate
    if not mean_weights:
        raise GraphBuildError("No positive finite edge weights after aggregation")

    graph = nx.Graph()
    query_nodes: set[str] = set()
    doc_nodes: set[str] = set()
    for (query, canonical_result_id), weight in mean_weights.items():
        query_node = f"query:{query}"
        document_node = f"doc:{canonical_result_id}"
        query_nodes.add(query_node)
        doc_nodes.add(document_node)
        graph.add_edge(query_node, document_node, weight=weight)
    if not query_nodes or not doc_nodes:
        raise GraphBuildError("Supervised graph has empty node partition")

    topology_graph = nx.Graph()
    topology_observations: list[dict[str, object]] = []
    for query_raw, link, stored_cid, recorded_at_epoch in exposure_rows:
        query = (query_raw or "").strip()
        link_value = (link or "").strip()
        canonical_result_id = (stored_cid or "").strip() or _canonical_result_id(link_value)
        if not query or not link_value or not canonical_result_id:
            continue
        topology_graph.add_edge(
            f"query:{query}",
            f"doc:{canonical_result_id}",
            weight=1.0,
        )
        topology_observations.append(
            {
                "canonical_result_id": canonical_result_id,
                "query": query,
                "recorded_at_epoch": recorded_at_epoch,
            }
        )
    if not topology_observations:
        for observation in retained_observations:
            query = str(observation["query"])
            canonical_result_id = str(observation["canonical_result_id"])
            topology_graph.add_edge(
                f"query:{query}",
                f"doc:{canonical_result_id}",
                weight=1.0,
            )
            topology_observations.append(
                {
                    "canonical_result_id": canonical_result_id,
                    "query": query,
                    "recorded_at_epoch": observation["recorded_at_epoch"],
                }
            )
    topology_query_nodes = {
        node for node in topology_graph if str(node).startswith("query:")
    }
    topology_doc_nodes = {
        node for node in topology_graph if str(node).startswith("doc:")
    }
    if not topology_query_nodes or not topology_doc_nodes:
        raise GraphBuildError("Result topology has empty node partition")

    try:
        birank_scores = nx.bipartite.birank(
            graph, query_nodes, weight="weight", max_iter=100, tol=1e-6
        )
        pagerank_scores = nx.pagerank(graph, weight="weight")
    except Exception as exc:
        raise GraphBuildError(f"Link analysis algorithm failed: {exc}") from exc

    source_fingerprint_payload = {
        "canonicalization_version": config.canonicalization_version,
        "label_version": config.label_version,
        "lookback_days": config.lookback_days,
        "max_related_queries": config.max_related_queries,
        "min_shared_documents": config.min_shared_documents,
        "observations": sorted(
            retained_observations,
            key=lambda observation: json.dumps(
                observation, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            ),
        ),
        "topology_observations": sorted(
            topology_observations,
            key=lambda observation: json.dumps(
                observation, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            ),
        ),
        "scoring_policy_version": config.scoring_policy_version,
        "source_cutoff": cutoff.isoformat(),
    }
    source_fingerprint = sha256(
        json.dumps(
            source_fingerprint_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    generation_id = (
        f"gen_{built_at.strftime('%Y%m%d_%H%M%S')}_"
        f"{sha256(f'{built_at.isoformat()}_{uuid.uuid4().hex}'.encode()).hexdigest()[:12]}"
    )

    result_features: list[dict[str, object]] = []
    for document_node in doc_nodes:
        canonical_result_id = document_node.removeprefix("doc:")
        result_features.append(
            {
                "generation_id": generation_id,
                "canonical_result_id": canonical_result_id,
                "birank_score": float(birank_scores.get(document_node, 0.0)),
                "pagerank_score": float(pagerank_scores.get(document_node, 0.0)),
                "weighted_degree": float(
                    sum(
                        data.get("weight", 1.0)
                        for _, _, data in graph.edges(document_node, data=True)
                    )
                ),
                "built_at": built_at,
            }
        )

    try:
        projected = nx.bipartite.overlap_weighted_projected_graph(
            topology_graph, topology_query_nodes, jaccard=True
        )
    except Exception as exc:
        raise GraphBuildError(f"Overlap weighted projection failed: {exc}") from exc

    candidate_pairs: list[tuple[str, str]] = []
    pair_support: dict[tuple[str, str], int] = {}
    for query_node, related_node in projected.edges():
        query = query_node.removeprefix("query:").strip()
        related = related_node.removeprefix("query:").strip()
        shared = set(topology_graph.neighbors(query_node)) & set(
            topology_graph.neighbors(related_node)
        )
        support = len(shared)
        if query and related and query != related and support >= config.min_shared_documents:
            candidate_pairs.append((query_node, related_node))
            pair_support[(query_node, related_node)] = support
            pair_support[(related_node, query_node)] = support

    aa_by_query: dict[str, list[tuple[str, float, int]]] = {}
    if candidate_pairs:
        try:
            for query_node, related_node, score in nx.adamic_adar_index(
                topology_graph, ebunch=candidate_pairs
            ):
                query = query_node.removeprefix("query:").strip()
                related = related_node.removeprefix("query:").strip()
                support = pair_support.get((query_node, related_node), 0)
                aa_by_query.setdefault(query, []).append((related, float(score), support))
                aa_by_query.setdefault(related, []).append((query, float(score), support))
        except Exception as exc:
            raise GraphBuildError(f"Adamic-Adar computation failed: {exc}") from exc

    neighbor_rows: list[dict[str, object]] = []
    for query, candidates in aa_by_query.items():
        candidates.sort(key=lambda item: (-item[1], -item[2], item[0].casefold()))
        for rank, (related, score, support) in enumerate(
            candidates[: config.max_related_queries], start=1
        ):
            neighbor_rows.append(
                {
                    "generation_id": generation_id,
                    "query_norm": query,
                    "related_norm": related,
                    "rank": rank,
                    "score": float(score),
                    "method": "adamic_adar_exposure_topology",
                    "support_count": int(support),
                    "built_at": built_at,
                }
            )

    shared_document_count = sum(
        len(list(topology_graph.neighbors(node))) >= 2 for node in topology_doc_nodes
    )
    observed_epochs = [
        float(observation["recorded_at_epoch"])
        for observation in retained_observations
        if isinstance(observation["recorded_at_epoch"], (int, float))
        and math.isfinite(float(observation["recorded_at_epoch"]))
    ]
    edge_weights = sorted(mean_weights.values())
    midpoint = len(edge_weights) // 2
    median_edge_weight = (
        edge_weights[midpoint]
        if len(edge_weights) % 2
        else (edge_weights[midpoint - 1] + edge_weights[midpoint]) / 2.0
    )
    support_histogram: dict[str, int] = {}
    for candidates in aa_by_query.values():
        for _, _, support in candidates:
            support_histogram[str(support)] = support_histogram.get(str(support), 0) + 1
    topology_degrees = {
        str(node): len(list(topology_graph.neighbors(node))) for node in topology_graph
    }
    return GraphSnapshot(
        generation_id=generation_id,
        built_at=built_at,
        source_cutoff=cutoff,
        label_version=config.label_version,
        source_fingerprint=source_fingerprint,
        config={
            "canonicalization_version": config.canonicalization_version,
            "deduplicated_observation_count": sum(
                len(weights_by_run) for weights_by_run in weights_by_pair_run.values()
            ),
            "edge_weight_max": edge_weights[-1],
            "edge_weight_median": median_edge_weight,
            "edge_weight_min": edge_weights[0],
            "feature_row_count": len(result_features),
            "label_version": config.label_version,
            "lookback_days": config.lookback_days,
            "max_related_queries": config.max_related_queries,
            "min_shared_documents": config.min_shared_documents,
            "neighbor_row_count": len(neighbor_rows),
            "retained_observation_count": len(retained_observations),
            "scoring_policy_version": config.scoring_policy_version,
            "source_cutoff": cutoff.isoformat(),
            "source_freshness_seconds": (
                max(0.0, cutoff.timestamp() - max(observed_epochs)) if observed_epochs else None
            ),
            "source_fingerprint": source_fingerprint,
            "shared_document_support_histogram": support_histogram,
            "supervised_edge_count": graph.number_of_edges(),
            "topology_edge_count": topology_graph.number_of_edges(),
            "topology_observation_count": len(topology_observations),
            "zero_degree_document_count": sum(
                topology_degrees.get(node, 0) == 0 for node in topology_doc_nodes
            ),
            "zero_degree_query_count": sum(
                topology_degrees.get(node, 0) == 0 for node in topology_query_nodes
            ),
        },
        query_node_count=len(topology_query_nodes),
        document_node_count=len(topology_doc_nodes),
        edge_count=topology_graph.number_of_edges(),
        shared_document_count=shared_document_count,
        neighbors=tuple(neighbor_rows),
        result_features=tuple(result_features),
    )

def _parse_utc_cutoff(raw_value: str) -> datetime:
    try:
        cutoff = datetime.fromisoformat(raw_value)
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 cutoff: {exc}") from exc
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("cutoff must include an explicit UTC offset")
    if cutoff.utcoffset() != timedelta(0):
        raise ValueError("cutoff must be expressed in UTC")
    return cutoff.astimezone(timezone.utc)


def generate_graph_snapshot(
    *,
    db_path: str | None,
    sqlite_path: str,
    config: GraphBuildConfig,
) -> GraphSnapshot:
    """Read DuckDB facts and publish one NetworkX snapshot to SQLite."""
    snapshot = build_graph_snapshot(db_path=db_path, config=config)
    publish_graph_snapshot(snapshot, sqlite_path=sqlite_path)
    return snapshot


def _snapshot_summary(
    snapshot: GraphSnapshot, *, sqlite_path: str, lookback_days: int
) -> dict[str, object]:
    return {
        "sqlite_path": sqlite_path,
        "generation_id": snapshot.generation_id,
        "source_cutoff": snapshot.source_cutoff.isoformat(),
        "source_fingerprint": snapshot.source_fingerprint,
        "lookback_days": lookback_days,
        "query_node_count": snapshot.query_node_count,
        "document_node_count": snapshot.document_node_count,
        "edge_count": snapshot.edge_count,
        "shared_document_count": snapshot.shared_document_count,
        "neighbor_row_count": len(snapshot.neighbors),
        "result_feature_row_count": len(snapshot.result_features),
        "metrics": snapshot.config,
    }


def compare_graph_windows(
    *,
    db_path: str | None,
    sqlite_dir: str,
    cutoff: datetime,
    windows: tuple[int, ...] = (30, 60, 90),
    label_version: str = "v1",
    min_shared_documents: int = 2,
    max_related_queries: int = 5,
) -> tuple[dict[str, object], ...]:
    """Generate isolated SQLite artifacts for the requested lookback windows."""
    output_dir = Path(sqlite_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, object]] = []
    for lookback_days in windows:
        if lookback_days <= 0:
            raise ValueError("lookback windows must be positive")
        sqlite_path = output_dir / f"graph_{lookback_days}d.sqlite"
        config = GraphBuildConfig(
            source_cutoff=cutoff,
            label_version=label_version,
            lookback_days=lookback_days,
            min_shared_documents=min_shared_documents,
            max_related_queries=max_related_queries,
        )
        snapshot = generate_graph_snapshot(
            db_path=db_path,
            sqlite_path=str(sqlite_path),
            config=config,
        )
        summaries.append(
            _snapshot_summary(
                snapshot,
                sqlite_path=str(sqlite_path),
                lookback_days=lookback_days,
            )
        )
    return tuple(summaries)


def main(argv: Sequence[str] | None = None) -> int:
    """Run graph generation, window comparison, or read-only replay."""
    import sys

    parser = argparse.ArgumentParser(description="Graph feedback SQLite operations.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    replay_parser = subparsers.add_parser(
        "replay", help="Replay graph-expansion decisions without provider calls."
    )
    replay_parser.add_argument("--db-path", type=str, default=None, help="DuckDB search history path")
    replay_parser.add_argument("--sqlite-path", type=str, default=None, help="SQLite graph artifact path")

    generate_parser = subparsers.add_parser(
        "generate", help="Read DuckDB facts and publish one SQLite graph artifact."
    )
    generate_parser.add_argument("--db-path", type=str, default=None, help="DuckDB analytics path")
    generate_parser.add_argument("--sqlite-path", type=str, required=True, help="SQLite artifact path")
    generate_parser.add_argument("--cutoff", type=str, required=True, help="Explicit UTC cutoff")
    generate_parser.add_argument("--label-version", type=str, default="v1")
    generate_parser.add_argument("--lookback-days", type=int, default=60)
    generate_parser.add_argument("--min-shared-documents", type=int, default=2)
    generate_parser.add_argument("--max-related-queries", type=int, default=5)

    compare_parser = subparsers.add_parser(
        "compare", help="Generate isolated SQLite artifacts for multiple windows."
    )
    compare_parser.add_argument("--db-path", type=str, default=None, help="DuckDB analytics path")
    compare_parser.add_argument("--sqlite-dir", type=str, required=True, help="SQLite output directory")
    compare_parser.add_argument("--cutoff", type=str, required=True, help="Explicit UTC cutoff")
    compare_parser.add_argument("--windows", type=str, default="30,60,90")
    compare_parser.add_argument("--label-version", type=str, default="v1")
    compare_parser.add_argument("--min-shared-documents", type=int, default=2)
    compare_parser.add_argument("--max-related-queries", type=int, default=5)

    args = parser.parse_args(argv)
    try:
        if args.command == "replay":
            from .graph_replay import replay_graph_expansion

            report = replay_graph_expansion(
                db_path=args.db_path,
                sqlite_path=args.sqlite_path,
            )
            sys.stdout.write(json.dumps(report.to_dict(), sort_keys=True, default=str) + "\n")
            return 0

        cutoff = _parse_utc_cutoff(args.cutoff)
        if args.command == "generate":
            config = GraphBuildConfig(
                source_cutoff=cutoff,
                label_version=args.label_version,
                lookback_days=args.lookback_days,
                min_shared_documents=args.min_shared_documents,
                max_related_queries=args.max_related_queries,
            )
            snapshot = generate_graph_snapshot(
                db_path=args.db_path,
                sqlite_path=args.sqlite_path,
                config=config,
            )
            sys.stdout.write(
                json.dumps(
                    _snapshot_summary(
                        snapshot,
                        sqlite_path=args.sqlite_path,
                        lookback_days=args.lookback_days,
                    ),
                    sort_keys=True,
                    default=str,
                )
                + "\n"
            )
            return 0

        windows = tuple(int(value.strip()) for value in args.windows.split(",") if value.strip())
        summaries = compare_graph_windows(
            db_path=args.db_path,
            sqlite_dir=args.sqlite_dir,
            cutoff=cutoff,
            windows=windows,
            label_version=args.label_version,
            min_shared_documents=args.min_shared_documents,
            max_related_queries=args.max_related_queries,
        )
        sys.stdout.write(json.dumps({"windows": summaries}, sort_keys=True, default=str) + "\n")
        return 0
    except Exception as exc:
        sys.stderr.write(f"Graph operation failed: {exc}\n")
        return 1
if __name__ == "__main__":
    import sys

    sys.exit(main())




