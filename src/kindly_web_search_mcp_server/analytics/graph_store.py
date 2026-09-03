"""SQLite persistence for NetworkX graph snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import time


_SCHEMA_VERSION = 1
_CACHE_TTL_SECONDS = 60.0
_CACHE_LOCK = threading.Lock()
_CACHED_INDICES: dict[tuple[str, str, str], tuple[GraphIndex, float]] = {}


@dataclass(frozen=True, slots=True)
class GraphSnapshot:
    generation_id: str
    built_at: datetime
    source_cutoff: datetime
    label_version: str
    source_fingerprint: str
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
    source_fingerprint: str
    config: dict[str, object]
    neighbors: dict[str, tuple[str, ...]]
    neighbor_supports: dict[str, dict[str, int]]
    result_features: dict[str, dict[str, float]]


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS graph_generations (
    generation_id TEXT PRIMARY KEY,
    built_at TEXT NOT NULL,
    source_cutoff TEXT NOT NULL,
    label_version TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('building', 'ready')),
    config_json TEXT NOT NULL,
    query_node_count INTEGER NOT NULL,
    document_node_count INTEGER NOT NULL,
    edge_count INTEGER NOT NULL,
    shared_document_count INTEGER NOT NULL,
    neighbor_row_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS graph_query_neighbors (
    generation_id TEXT NOT NULL,
    query_norm TEXT NOT NULL,
    related_norm TEXT NOT NULL,
    rank INTEGER NOT NULL,
    score REAL NOT NULL,
    method TEXT NOT NULL,
    support_count INTEGER NOT NULL,
    PRIMARY KEY (generation_id, query_norm, related_norm, method),
    FOREIGN KEY (generation_id) REFERENCES graph_generations(generation_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_result_features (
    generation_id TEXT NOT NULL,
    canonical_result_id TEXT NOT NULL,
    birank_score REAL NOT NULL,
    pagerank_score REAL NOT NULL,
    weighted_degree REAL NOT NULL,
    PRIMARY KEY (generation_id, canonical_result_id),
    FOREIGN KEY (generation_id) REFERENCES graph_generations(generation_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_graph_generations_ready
    ON graph_generations(status, built_at DESC);
CREATE INDEX IF NOT EXISTS idx_graph_neighbors_lookup
    ON graph_query_neighbors(generation_id, query_norm, rank);
CREATE INDEX IF NOT EXISTS idx_graph_features_lookup
    ON graph_result_features(generation_id, canonical_result_id);
"""


def default_graph_store_path(analytics_db_path: str | None = None) -> Path:
    """Derive the SQLite artifact path beside the configured analytics database."""
    if analytics_db_path is None:
        from ..settings import settings

        analytics_db_path = settings.analytics_duckdb_path
    return Path(analytics_db_path).with_suffix(".graph.sqlite")


def _connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
    if read_only:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(path), timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    if not read_only:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version > _SCHEMA_VERSION:
        raise RuntimeError(
            f"Unsupported graph SQLite schema version {version}; expected <= {_SCHEMA_VERSION}"
        )
    connection.executescript(_SCHEMA_SQL)
    if version < _SCHEMA_VERSION:
        connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("graph timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored graph timestamp is not timezone-aware")
    return parsed.astimezone(timezone.utc)


def publish_graph_snapshot(snapshot: GraphSnapshot, *, sqlite_path: str | None) -> None:
    """Publish one ready graph generation atomically to SQLite."""
    if not sqlite_path:
        raise ValueError("sqlite_path is required for graph publication")
    path = Path(sqlite_path)
    connection = _connect(path, read_only=False)
    try:
        _ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO graph_generations (
                generation_id, built_at, source_cutoff, label_version, algorithm, status,
                config_json, query_node_count, document_node_count, edge_count,
                shared_document_count, neighbor_row_count
            ) VALUES (?, ?, ?, ?, ?, 'building', ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.generation_id,
                _timestamp(snapshot.built_at),
                _timestamp(snapshot.source_cutoff),
                snapshot.label_version,
                "adamic_adar_birank",
                json.dumps(snapshot.config, sort_keys=True, separators=(",", ":")),
                snapshot.query_node_count,
                snapshot.document_node_count,
                snapshot.edge_count,
                snapshot.shared_document_count,
                len(snapshot.neighbors),
            ),
        )
        connection.executemany(
            """
            INSERT INTO graph_query_neighbors (
                generation_id, query_norm, related_norm, rank, score, method, support_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    snapshot.generation_id,
                    str(row["query_norm"]),
                    str(row["related_norm"]),
                    int(str(row["rank"])),
                    float(str(row["score"])),
                    str(row["method"]),
                    int(str(row["support_count"])),
                )
                for row in snapshot.neighbors
            ],
        )
        connection.executemany(
            """
            INSERT INTO graph_result_features (
                generation_id, canonical_result_id, birank_score, pagerank_score, weighted_degree
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    snapshot.generation_id,
                    str(row["canonical_result_id"]),
                    float(str(row["birank_score"])),
                    float(str(row["pagerank_score"])),
                    float(str(row["weighted_degree"])),
                )
                for row in snapshot.result_features
            ],
        )
        connection.execute(
            "UPDATE graph_generations SET status = 'ready' WHERE generation_id = ?",
            (snapshot.generation_id,),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def load_latest_graph_index(
    *,
    sqlite_path: str | None,
    max_age_seconds: float,
    expected_label_version: str = "v1",
    expected_scoring_policy_version: str = "judge_gain_confidence_mean_v1",
) -> GraphIndex | None:
    """Load the newest compatible ready generation from SQLite."""
    if max_age_seconds < 0:
        return None
    path = default_graph_store_path() if sqlite_path is None else Path(sqlite_path)
    if not path.exists():
        return None
    cache_key = (
        str(path.resolve()),
        expected_label_version,
        expected_scoring_policy_version,
    )
    now_monotonic = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHED_INDICES.get(cache_key)
        if cached is not None and now_monotonic - cached[1] < _CACHE_TTL_SECONDS:
            index = cached[0]
            if (datetime.now(timezone.utc) - index.built_at).total_seconds() <= max_age_seconds:
                return index
            return None

    try:
        connection = _connect(path, read_only=True)
    except (OSError, sqlite3.Error):
        return None
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != _SCHEMA_VERSION:
            return None
        generation = connection.execute(
            """
            SELECT generation_id, built_at, source_cutoff, label_version, config_json
            FROM graph_generations
            WHERE status = 'ready'
            ORDER BY built_at DESC, generation_id DESC
            LIMIT 1
            """
        ).fetchone()
        if generation is None:
            return None
        config = json.loads(str(generation["config_json"]))
        if not isinstance(config, dict):
            return None
        if (
            generation["label_version"] != expected_label_version
            or config.get("scoring_policy_version") != expected_scoring_policy_version
            or not isinstance(config.get("source_fingerprint"), str)
        ):
            return None
        built_at = _parse_timestamp(str(generation["built_at"]))
        if (datetime.now(timezone.utc) - built_at).total_seconds() > max_age_seconds:
            return None
        generation_id = str(generation["generation_id"])
        neighbors: dict[str, list[str]] = {}
        neighbor_supports: dict[str, dict[str, int]] = {}
        for row in connection.execute(
            """
            SELECT query_norm, related_norm, support_count
            FROM graph_query_neighbors
            WHERE generation_id = ?
            ORDER BY query_norm, rank, related_norm
            """,
            (generation_id,),
        ):
            query = str(row["query_norm"])
            related = str(row["related_norm"])
            neighbors.setdefault(query, []).append(related)
            neighbor_supports.setdefault(query, {})[related] = int(row["support_count"])
        result_features = {
            str(row["canonical_result_id"]): {
                "birank_score": float(row["birank_score"]),
                "pagerank_score": float(row["pagerank_score"]),
                "weighted_degree": float(row["weighted_degree"]),
            }
            for row in connection.execute(
                """
                SELECT canonical_result_id, birank_score, pagerank_score, weighted_degree
                FROM graph_result_features
                WHERE generation_id = ?
                """,
                (generation_id,),
            )
        }
        index = GraphIndex(
            generation_id=generation_id,
            built_at=built_at,
            source_cutoff=_parse_timestamp(str(generation["source_cutoff"])),
            label_version=str(generation["label_version"]),
            source_fingerprint=str(config["source_fingerprint"]),
            config=config,
            neighbors={query: tuple(values) for query, values in neighbors.items()},
            neighbor_supports=neighbor_supports,
            result_features=result_features,
        )
        with _CACHE_LOCK:
            _CACHED_INDICES[cache_key] = (index, time.monotonic())
        return index
    except (OSError, sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
        return None
    finally:
        connection.close()
