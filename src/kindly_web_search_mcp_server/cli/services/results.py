from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .jobs import jobs_db_path

LOGGER = logging.getLogger(__name__)

RESULT_KINDS = frozenset({"mcp", "cli", "deep_research"})
TTL_SECONDS = 24 * 60 * 60
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


def result_db_path(db_path: str | Path | None = None) -> Path:
    """Return the shared SQLite path used by jobs and stored results."""
    return Path(db_path).expanduser() if db_path is not None else jobs_db_path()


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = result_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=10.0)
    connection.row_factory = sqlite3.Row
    # Keep these settings identical on every connection to the shared store.
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA cache_size=-64000")
    connection.execute("PRAGMA mmap_size=268435456")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS results (
            result_id INTEGER PRIMARY KEY AUTOINCREMENT,
            result_kind TEXT NOT NULL CHECK (result_kind IN ('mcp', 'cli', 'deep_research')),
            source TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER,
            payload_json TEXT NOT NULL,
            search_text TEXT NOT NULL,
            CHECK (
                (result_kind IN ('mcp', 'cli') AND expires_at IS NOT NULL)
                OR (result_kind = 'deep_research' AND expires_at IS NULL)
            )
        );
        CREATE INDEX IF NOT EXISTS idx_results_kind_created
            ON results(result_kind, created_at DESC, result_id DESC);
        CREATE INDEX IF NOT EXISTS idx_results_source_created
            ON results(source, created_at DESC, result_id DESC);
        CREATE INDEX IF NOT EXISTS idx_results_expires_at
            ON results(expires_at);
        """
    )
    try:
        connection.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS results_fts USING fts5(
                result_kind UNINDEXED,
                source,
                search_text,
                content='results',
                content_rowid='result_id'
            );
            CREATE TRIGGER IF NOT EXISTS results_after_insert
            AFTER INSERT ON results BEGIN
                INSERT INTO results_fts(rowid, result_kind, source, search_text)
                VALUES (new.result_id, new.result_kind, new.source, new.search_text);
            END;
            CREATE TRIGGER IF NOT EXISTS results_after_delete
            AFTER DELETE ON results BEGIN
                INSERT INTO results_fts(results_fts, rowid, result_kind, source, search_text)
                VALUES ('delete', old.result_id, old.result_kind, old.source, old.search_text);
            END;
            """
        )
    except sqlite3.OperationalError as exc:
        # FTS5 is optional in some Python builds. Search falls back to LIKE.
        LOGGER.debug("SQLite FTS5 unavailable for result store: %s", exc)
    return connection


def _now_epoch() -> int:
    return int(time.time())


def _iso_timestamp(epoch: int | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _encode_payload(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default)


def _searchable_text(source: str, result_kind: str, payload_json: str) -> str:
    return f"{result_kind} {source} {payload_json}"


def _validate_kind(result_kind: str) -> None:
    if result_kind not in RESULT_KINDS:
        allowed = ", ".join(sorted(RESULT_KINDS))
        raise ValueError(f"Unsupported result kind {result_kind!r}; expected one of: {allowed}.")


def cleanup_expired_results(
    *, db_path: str | Path | None = None, now: int | None = None
) -> int:
    """Delete expired MCP/CLI rows and return the number removed."""
    connection = _connect(db_path)
    try:
        with connection:
            cursor = connection.execute(
                "DELETE FROM results WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (now if now is not None else _now_epoch(),),
            )
        return cursor.rowcount
    finally:
        connection.close()


def store_result(
    result_kind: str,
    source: str,
    payload: Any,
    *,
    db_path: str | Path | None = None,
    created_at: int | None = None,
) -> dict[str, Any]:
    """Store one result with the requested retention policy."""
    _validate_kind(result_kind)
    if not source.strip():
        raise ValueError("Result source must be a non-blank string.")

    created = created_at if created_at is not None else _now_epoch()
    expires = created + TTL_SECONDS if result_kind in {"mcp", "cli"} else None
    payload_json = _encode_payload(payload)
    search_text = _searchable_text(source, result_kind, payload_json)
    connection = _connect(db_path)
    try:
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO results (
                    result_kind, source, created_at, expires_at, payload_json, search_text
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (result_kind, source, created, expires, payload_json, search_text),
            )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return a result id.")
        result_id = int(cursor.lastrowid)
    finally:
        connection.close()

    return {
        "result_id": result_id,
        "result_kind": result_kind,
        "source": source,
        "created_at": _iso_timestamp(created),
        "expires_at": _iso_timestamp(expires),
    }


def persist_result_best_effort(
    result_kind: str,
    source: str,
    payload: Any,
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Persist a result without changing the caller's success/failure path."""
    try:
        return store_result(result_kind, source, payload, db_path=db_path)
    except Exception:  # pragma: no cover - defensive boundary around optional storage
        LOGGER.warning("Could not persist %s result from %s", result_kind, source, exc_info=True)
        return None


def persist_cli_result(
    command: str, payload: Any, *, db_path: str | Path | None = None
) -> dict[str, Any] | None:
    result_kind = "deep_research" if command == "research deep" else "cli"
    return persist_result_best_effort(result_kind, command, payload, db_path=db_path)


def persist_mcp_result(
    tool_name: str, payload: Any, *, db_path: str | Path | None = None
) -> dict[str, Any] | None:
    result_kind = "deep_research" if tool_name == "deep_research" else "mcp"
    return persist_result_best_effort(result_kind, tool_name, payload, db_path=db_path)


def _fts_query(query: str) -> str:
    tokens = re.findall(r"[\w]+", query, flags=re.UNICODE)
    return " AND ".join('"' + token.replace('"', '""') + '"' for token in tokens)


def _decode_payload(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _row_to_result(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "result_id": int(row["result_id"]),
        "result_kind": row["result_kind"],
        "source": row["source"],
        "created_at": _iso_timestamp(int(row["created_at"])),
        "expires_at": _iso_timestamp(int(row["expires_at"]))
        if row["expires_at"] is not None
        else None,
        "payload": _decode_payload(row["payload_json"]),
    }


def search_results(
    query: str = "",
    *,
    result_kind: str | None = None,
    source: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    db_path: str | Path | None = None,
    now: int | None = None,
) -> list[dict[str, Any]]:
    """Search retained result payloads, cleaning expired rows first."""
    if result_kind is not None:
        _validate_kind(result_kind)
    safe_limit = max(1, min(limit, _MAX_LIMIT))
    connection = _connect(db_path)
    try:
        with connection:
            connection.execute(
                "DELETE FROM results WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (now if now is not None else _now_epoch(),),
            )

        clauses: list[str] = []
        params: list[Any] = []
        fts_query = _fts_query(query) if query.strip() else ""
        use_fts = bool(fts_query)
        if use_fts:
            clauses.append("results_fts MATCH ?")
            params.append(fts_query)
        if result_kind is not None:
            clauses.append("r.result_kind = ?")
            params.append(result_kind)
        if source is not None:
            clauses.append("r.source = ?")
            params.append(source)

        where = " AND ".join(clauses) or "1=1"
        if use_fts:
            sql = f"""
                SELECT r.result_id, r.result_kind, r.source, r.created_at,
                       r.expires_at, r.payload_json
                FROM results AS r
                JOIN results_fts ON results_fts.rowid = r.result_id
                WHERE {where}
                ORDER BY r.created_at DESC, r.result_id DESC
                LIMIT ?
            """
        else:
            if query.strip():
                terms = [term for term in re.split(r"\s+", query.strip()) if term]
                clauses.extend(["r.search_text LIKE ?"] * len(terms))
                params.extend(f"%{term}%" for term in terms)
                where = " AND ".join(clauses)
            sql = f"""
                SELECT r.result_id, r.result_kind, r.source, r.created_at,
                       r.expires_at, r.payload_json
                FROM results AS r
                WHERE {where}
                ORDER BY r.created_at DESC, r.result_id DESC
                LIMIT ?
            """
        params.append(safe_limit)
        rows = connection.execute(sql, params).fetchall()
        return [_row_to_result(row) for row in rows]
    finally:
        connection.close()


__all__ = [
    "RESULT_KINDS",
    "TTL_SECONDS",
    "cleanup_expired_results",
    "persist_cli_result",
    "persist_mcp_result",
    "persist_result_best_effort",
    "result_db_path",
    "search_results",
    "store_result",
]
