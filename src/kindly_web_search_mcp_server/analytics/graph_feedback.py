"""Offline graph feedback loop: build, publish, and load latest graph index."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import threading
import time
import uuid

import duckdb

from .async_writes import dispatch_duckdb_write
from .feedback_labels import materialize_result_labels
from .quality_metrics import compute_positional_discount
from .writers.connection import _LOCK, _db_path


class GraphBuildError(RuntimeError):
    """Raised when graph construction or convergence fails."""


@dataclass(frozen=True, slots=True)
class GraphBuildConfig:
    source_cutoff: datetime
    label_version: str = "v1"
    min_shared_documents: int = 2
    max_related_queries: int = 5


@dataclass(frozen=True, slots=True)
class GraphSnapshot:
    generation_id: str
    built_at: datetime
    source_cutoff: datetime
    label_version: str
    config: dict[str, object]
    query_node_count: int
    document_node_count: int
    edge_count: int
    shared_document_count: int
    neighbors: tuple[dict[str, object], ...]
    result_features: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class GraphIndex:
    generation_id: str
    built_at: datetime
    source_cutoff: datetime
    label_version: str
    neighbors: dict[str, tuple[str, ...]]


_CACHE_LOCK = threading.Lock()
_CACHED_INDEX: GraphIndex | None = None
_CACHED_AT: float = 0.0
_CACHE_TTL_SECONDS = 60.0


def build_graph_snapshot(*, db_path: str | None, config: GraphBuildConfig) -> GraphSnapshot:
    """Build an in-memory bipartite graph and compute BiRank, PageRank, and query neighbors."""
    try:
        import networkx as nx
    except ImportError as exc:
        raise GraphBuildError(f"NetworkX is required for graph building: {exc}") from exc

    cutoff = config.source_cutoff
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)

    built_at = datetime.now(timezone.utc)
    path = _db_path(db_path)
    if not path.exists():
        raise GraphBuildError(f"Database path does not exist: {path}")

    con = duckdb.connect(str(path), read_only=True)
    try:
        table_rows = con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
        table_names = {row[0] for row in table_rows}
        if "result_labels" not in table_names or "search_runs" not in table_names:
            raise GraphBuildError("Required tables result_labels or search_runs missing")

        rows = con.execute(
            """
            SELECT
                sr.normalized_query,
                rl.canonical_result_id,
                rl.discounted_gain,
                rl.label,
                rl.position,
                rl.payload_json
            FROM result_labels rl
            JOIN search_runs sr ON rl.run_key = sr.run_key
            WHERE rl.source = 'llm_judge'
              AND rl.rubric_version = ?
              AND rl.recorded_at <= to_timestamp(?)
              AND sr.normalized_query IS NOT NULL
              AND trim(sr.normalized_query) != ''
              AND rl.canonical_result_id IS NOT NULL
              AND trim(rl.canonical_result_id) != ''
            """,
            [config.label_version, cutoff.timestamp()],
        ).fetchall()
    finally:
        con.close()

    if not rows:
        raise GraphBuildError("No result_labels rows found for the given criteria")

    weights_by_pair: dict[tuple[str, str], list[float]] = {}
    for q_norm, cid, disc_gain, lbl, pos, raw_payload in rows:
        q = (q_norm or "").strip()
        c = (cid or "").strip()
        if not q or not c:
            continue

        if disc_gain is None:
            l_val = float(lbl or 0.0)
            p_val = int(pos if pos is not None and not isinstance(pos, bool) else 0)
            gain = compute_positional_discount(l_val, p_val)
        else:
            gain = float(disc_gain)

        conf_fraction = 1.0
        if raw_payload is not None:
            try:
                p_dict = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
                if isinstance(p_dict, dict):
                    if "confidence_fraction" in p_dict:
                        conf_fraction = float(p_dict["confidence_fraction"])
                    elif "parsed" in p_dict and isinstance(p_dict["parsed"], dict):
                        conf_int = p_dict["parsed"].get("confidence")
                        if isinstance(conf_int, int) and not isinstance(conf_int, bool):
                            conf_fraction = conf_int / 4.0
            except Exception:
                conf_fraction = 1.0

        clamped_conf = min(max(conf_fraction, 0.0), 1.0)
        weight = max(gain, 0.0) * clamped_conf
        weights_by_pair.setdefault((q, c), []).append(weight)

    # Arithmetic mean aggregation
    mean_weights: dict[tuple[str, str], float] = {}
    for pair, ws in weights_by_pair.items():
        avg_w = sum(ws) / len(ws)
        if avg_w > 0.0:
            mean_weights[pair] = avg_w

    if not mean_weights:
        raise GraphBuildError("No positive edge weights after aggregation")

    graph = nx.Graph()
    query_nodes: set[str] = set()
    doc_nodes: set[str] = set()

    for (q, c), w in mean_weights.items():
        q_node = f"query:{q}"
        d_node = f"doc:{c}"
        query_nodes.add(q_node)
        doc_nodes.add(d_node)
        graph.add_edge(q_node, d_node, weight=w)

    if not query_nodes or not doc_nodes or graph.number_of_edges() == 0:
        raise GraphBuildError("Bipartite graph has empty node partition or no edges")

    # Link analysis
    try:
        birank_scores = nx.bipartite.birank(
            graph, query_nodes, weight="weight", max_iter=100, tol=1e-6
        )
        pagerank_scores = nx.pagerank(graph, weight="weight")
    except Exception as exc:
        raise GraphBuildError(f"Link analysis algorithm failed: {exc}") from exc

    gen_id_entropy = (
        f"{built_at.isoformat()}_{len(query_nodes)}_{len(doc_nodes)}_{uuid.uuid4().hex[:8]}"
    )
    generation_id = f"gen_{built_at.strftime('%Y%m%d_%H%M%S')}_{sha256(gen_id_entropy.encode()).hexdigest()[:12]}"

    result_features: list[dict[str, object]] = []
    for d_node in doc_nodes:
        cid = d_node.removeprefix("doc:")
        b_score = float(birank_scores.get(d_node, 0.0))
        p_score = float(pagerank_scores.get(d_node, 0.0))
        w_deg = sum(data.get("weight", 1.0) for _, _, data in graph.edges(d_node, data=True))
        result_features.append(
            {
                "generation_id": generation_id,
                "canonical_result_id": cid,
                "birank_score": b_score,
                "pagerank_score": p_score,
                "weighted_degree": float(w_deg),
                "built_at": built_at,
            }
        )

    # Overlap projection for candidate query pairs
    try:
        projected = nx.bipartite.overlap_weighted_projected_graph(graph, query_nodes, jaccard=True)
    except Exception as exc:
        raise GraphBuildError(f"Overlap weighted projection failed: {exc}") from exc

    candidate_pairs: list[tuple[str, str]] = []
    pair_support: dict[tuple[str, str], int] = {}
    for u, v in projected.edges():
        q1 = u.removeprefix("query:").strip()
        q2 = v.removeprefix("query:").strip()
        if not q1 or not q2 or q1 == q2:
            continue
        shared = set(graph.neighbors(u)) & set(graph.neighbors(v))
        support = len(shared)
        if support >= config.min_shared_documents:
            candidate_pairs.append((u, v))
            pair_support[(u, v)] = support
            pair_support[(v, u)] = support

    aa_by_query: dict[str, list[tuple[str, float, int]]] = {}
    if candidate_pairs:
        try:
            aa_results = nx.adamic_adar_index(graph, ebunch=candidate_pairs)
            for u, v, score in aa_results:
                q1 = u.removeprefix("query:").strip()
                q2 = v.removeprefix("query:").strip()
                supp = pair_support.get((u, v), 0)
                aa_by_query.setdefault(q1, []).append((q2, float(score), supp))
                aa_by_query.setdefault(q2, []).append((q1, float(score), supp))
        except Exception as exc:
            raise GraphBuildError(f"Adamic-Adar computation failed: {exc}") from exc

    neighbor_rows: list[dict[str, object]] = []
    for q_source, candidates in aa_by_query.items():
        candidates.sort(key=lambda item: (-item[1], -item[2], item[0].casefold()))
        for rank, (q_rel, score, supp) in enumerate(
            candidates[: config.max_related_queries], start=1
        ):
            neighbor_rows.append(
                {
                    "generation_id": generation_id,
                    "query_norm": q_source,
                    "related_norm": q_rel,
                    "rank": rank,
                    "score": float(score),
                    "method": "adamic_adar",
                    "support_count": int(supp),
                    "built_at": built_at,
                }
            )

    shared_document_count = sum(
        1 for d_node in doc_nodes if len(list(graph.neighbors(d_node))) >= 2
    )

    return GraphSnapshot(
        generation_id=generation_id,
        built_at=built_at,
        source_cutoff=cutoff,
        label_version=config.label_version,
        config={
            "min_shared_documents": config.min_shared_documents,
            "max_related_queries": config.max_related_queries,
            "label_version": config.label_version,
            "source_cutoff": cutoff.isoformat(),
        },
        query_node_count=len(query_nodes),
        document_node_count=len(doc_nodes),
        edge_count=graph.number_of_edges(),
        shared_document_count=shared_document_count,
        neighbors=tuple(neighbor_rows),
        result_features=tuple(result_features),
    )


def publish_graph_snapshot(snapshot: GraphSnapshot, *, db_path: str | None = None) -> None:
    """Publish graph generation atomically under _LOCK via dispatch_duckdb_write."""

    def _write_task() -> None:
        path = _db_path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            con = duckdb.connect(str(path), read_only=False)
            try:
                con.execute("BEGIN TRANSACTION;")
                if snapshot.neighbors:
                    con.executemany(
                        """
                        INSERT INTO graph_query_neighbors (
                            generation_id, query_norm, related_norm, rank, score, method, support_count, built_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, to_timestamp(?));
                        """,
                        [
                            (
                                r["generation_id"],
                                r["query_norm"],
                                r["related_norm"],
                                r["rank"],
                                r["score"],
                                r["method"],
                                r["support_count"],
                                r["built_at"].timestamp()
                                if isinstance(r["built_at"], datetime)
                                else float(str(r["built_at"])),
                            )
                            for r in snapshot.neighbors
                        ],
                    )

                if snapshot.result_features:
                    con.executemany(
                        """
                        INSERT INTO graph_result_features (
                            generation_id, canonical_result_id, birank_score, pagerank_score, weighted_degree, built_at
                        ) VALUES (?, ?, ?, ?, ?, to_timestamp(?));
                        """,
                        [
                            (
                                r["generation_id"],
                                r["canonical_result_id"],
                                r["birank_score"],
                                r["pagerank_score"],
                                r["weighted_degree"],
                                r["built_at"].timestamp()
                                if isinstance(r["built_at"], datetime)
                                else float(str(r["built_at"])),
                            )
                            for r in snapshot.result_features
                        ],
                    )

                con.execute(
                    """
                    INSERT INTO graph_feedback_generations (
                        generation_id, built_at, source_cutoff, label_version, algorithm, status,
                        config_json, query_node_count, document_node_count, edge_count,
                        shared_document_count, neighbor_row_count, error_type, error_message
                    ) VALUES (
                        ?, to_timestamp(?), to_timestamp(?), ?, 'adamic_adar_birank', 'ready',
                        ?, ?, ?, ?, ?, ?, NULL, NULL
                    );
                    """,
                    [
                        snapshot.generation_id,
                        snapshot.built_at.timestamp(),
                        snapshot.source_cutoff.timestamp(),
                        snapshot.label_version,
                        json.dumps(snapshot.config),
                        snapshot.query_node_count,
                        snapshot.document_node_count,
                        snapshot.edge_count,
                        snapshot.shared_document_count,
                        len(snapshot.neighbors),
                    ],
                )
                con.execute("COMMIT;")
            except Exception as exc:
                try:
                    con.execute("ROLLBACK;")
                except Exception:
                    pass
                try:
                    con.execute(
                        """
                        INSERT INTO graph_feedback_generations (
                            generation_id, built_at, source_cutoff, label_version, algorithm, status,
                            config_json, query_node_count, document_node_count, edge_count,
                            shared_document_count, neighbor_row_count, error_type, error_message
                        ) VALUES (
                            ?, to_timestamp(?), to_timestamp(?), ?, 'adamic_adar_birank', 'failed',
                            ?, ?, ?, ?, ?, ?, ?, ?
                        );
                        """,
                        [
                            snapshot.generation_id,
                            snapshot.built_at.timestamp(),
                            snapshot.source_cutoff.timestamp(),
                            snapshot.label_version,
                            json.dumps(snapshot.config),
                            snapshot.query_node_count,
                            snapshot.document_node_count,
                            snapshot.edge_count,
                            snapshot.shared_document_count,
                            len(snapshot.neighbors),
                            type(exc).__name__,
                            str(exc)[:500],
                        ],
                    )
                except Exception:
                    pass
                raise
            finally:
                con.close()

    fut = dispatch_duckdb_write("analytics.graph_feedback.publish", _write_task)
    fut.result()


def load_latest_graph_index(*, db_path: str | None, max_age_seconds: float) -> GraphIndex | None:
    """Load latest ready graph index within max_age_seconds, caching for 60s."""
    global _CACHED_INDEX, _CACHED_AT

    if max_age_seconds < 0:
        return None

    now_mono = time.monotonic()
    with _CACHE_LOCK:
        if _CACHED_INDEX is not None and (now_mono - _CACHED_AT) < _CACHE_TTL_SECONDS:
            age = (datetime.now(timezone.utc) - _CACHED_INDEX.built_at).total_seconds()
            if age <= max_age_seconds:
                return _CACHED_INDEX
            return None

    path = _db_path(db_path)
    if not path.exists():
        return None

    try:
        con = duckdb.connect(str(path), read_only=True)
        try:
            table_rows = con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
            table_names = {row[0] for row in table_rows}
            if (
                "graph_feedback_generations" not in table_names
                or "graph_query_neighbors" not in table_names
            ):
                return None

            gen_row = con.execute(
                """
                SELECT
                    generation_id,
                    epoch(built_at) AS built_at_epoch,
                    epoch(source_cutoff) AS source_cutoff_epoch,
                    label_version
                FROM graph_feedback_generations
                WHERE status = 'ready'
                ORDER BY built_at DESC
                LIMIT 1
                """
            ).fetchone()

            if not gen_row:
                return None

            gen_id, built_at_epoch, source_cutoff_epoch, label_ver = gen_row
            built_at = datetime.fromtimestamp(float(built_at_epoch), tz=timezone.utc)
            source_cutoff = datetime.fromtimestamp(float(source_cutoff_epoch), tz=timezone.utc)

            age_seconds = (datetime.now(timezone.utc) - built_at).total_seconds()
            if age_seconds > max_age_seconds:
                return None

            neighbor_rows = con.execute(
                """
                SELECT query_norm, related_norm, rank
                FROM graph_query_neighbors
                WHERE generation_id = ?
                ORDER BY query_norm, rank ASC
                """,
                [gen_id],
            ).fetchall()

            neighbors_map: dict[str, tuple[str, ...]] = {}
            for q_norm, rel_norm, _ in neighbor_rows:
                if q_norm:
                    cur = neighbors_map.get(q_norm, ())
                    neighbors_map[q_norm] = cur + (rel_norm,)

            index = GraphIndex(
                generation_id=gen_id,
                built_at=built_at,
                source_cutoff=source_cutoff,
                label_version=label_ver,
                neighbors=neighbors_map,
            )

            with _CACHE_LOCK:
                _CACHED_INDEX = index
                _CACHED_AT = time.monotonic()

            return index
        finally:
            con.close()
    except Exception:
        return None


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for graph feedback operations."""
    import sys

    parser = argparse.ArgumentParser(description="Graph feedback offline operations.")
    subparsers = parser.add_subparsers(dest="command")

    rebuild_parser = subparsers.add_parser("rebuild", help="Rebuild graph feedback generation.")
    rebuild_parser.add_argument("--db-path", type=str, default=None, help="DuckDB path")
    rebuild_parser.add_argument(
        "--cutoff", type=str, default=None, help="ISO-8601 source cutoff datetime"
    )
    rebuild_parser.add_argument(
        "--label-version", type=str, default="v1", help="Rubric / label version"
    )
    rebuild_parser.add_argument(
        "--min-shared-documents", type=int, default=2, help="Minimum shared documents"
    )
    rebuild_parser.add_argument(
        "--max-related-queries", type=int, default=5, help="Max related queries per query"
    )

    args = parser.parse_args(argv)
    if args.command != "rebuild":
        parser.print_help()
        return 1

    cutoff: datetime | None = None
    if args.cutoff:
        try:
            cutoff = datetime.fromisoformat(args.cutoff)
            if cutoff.tzinfo is None:
                cutoff = cutoff.replace(tzinfo=timezone.utc)
        except Exception as exc:
            sys.stderr.write(f"Invalid --cutoff ISO-8601 datetime: {exc}\n")
            return 2
    else:
        cutoff = datetime.now(timezone.utc)

    try:
        materialize_result_labels(
            db_path=args.db_path,
            source_cutoff=cutoff,
            rubric_version=args.label_version,
        )

        config = GraphBuildConfig(
            source_cutoff=cutoff,
            label_version=args.label_version,
            min_shared_documents=args.min_shared_documents,
            max_related_queries=args.max_related_queries,
        )
        snapshot = build_graph_snapshot(db_path=args.db_path, config=config)
        publish_graph_snapshot(snapshot, db_path=args.db_path)
        return 0
    except Exception as exc:
        sys.stderr.write(f"Rebuild failed: {exc}\n")
        return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
