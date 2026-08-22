from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import statistics
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb


EXPORT_TABLES = (
    "search_runs",
    "search_branches",
    "provider_calls",
    "search_candidates",
    "final_results",
    "rerank_stages",
    "search_quality_scores",
)


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if hasattr(value, "tolist"):
        return value.tolist()
    return str(value)


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=json_default) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, default=json_default) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"corrupt JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"non-object JSONL record at {path}:{line_number}")
            records.append(value)
    return records


def load_corpus(path: Path) -> tuple[list[dict[str, Any]], str]:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, list) or len(payload) != 50:
        raise ValueError("corpus must contain exactly 50 cases")
    ids = [case.get("id") for case in payload]
    if len(set(ids)) != 50:
        raise ValueError("corpus IDs must be unique")
    batches = Counter(case.get("batch") for case in payload)
    if batches != Counter({batch: 5 for batch in range(1, 11)}):
        raise ValueError("corpus must contain batches 1..10 with five cases each")
    for case in payload:
        if not all(
            str(case.get(field, "")).strip() for field in ("id", "topic", "query", "research_goal")
        ):
            raise ValueError(f"incomplete corpus case: {case!r}")
    return payload, hashlib.sha256(raw).hexdigest()


def table_rows(connection: duckdb.DuckDBPyConnection, table: str) -> list[dict[str, Any]]:
    schema = connection.execute(f'DESCRIBE "{table}"').fetchall()
    expressions = [
        f'CAST("{name}" AS VARCHAR) AS "{name}"' if "TIMESTAMP" in data_type else f'"{name}"'
        for name, data_type, *_ in schema
    ]
    result = connection.execute(
        f'SELECT {", ".join(expressions)} FROM "{table}" ORDER BY recorded_at'
    ).fetchall()
    columns = [item[0] for item in connection.description]
    return [dict(zip(columns, row, strict=True)) for row in result]


def _table_exists(connection: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(
        connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchone()[0]
    )


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _find_key(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = _find_key(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_key(child, key)
            if found is not None:
                return found
    return None


async def wait_for_databases(run_dir: Path, timeout_seconds: float = 360) -> None:
    paths = (run_dir / "analytics.duckdb", run_dir / "process_logs.duckdb")
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        if all(path.exists() for path in paths):
            try:
                for path in paths:
                    with duckdb.connect(str(path), read_only=True) as connection:
                        connection.execute("SELECT 1").fetchone()
                return
            except duckdb.Error:
                pass
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(
                "run-scoped DuckDB files did not become readable after server shutdown"
            )
        await asyncio.sleep(1)


def analytics_gate(run_dir: Path, batch_calls: list[dict[str, Any]]) -> dict[str, Any]:
    failures: list[str] = []
    analytics_path = run_dir / "analytics.duckdb"
    logs_path = run_dir / "process_logs.duckdb"
    if not analytics_path.exists():
        return {"passed": False, "failures": ["analytics.duckdb missing"]}
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        runs = (
            table_rows(connection, "search_runs")
            if _table_exists(connection, "search_runs")
            else []
        )
        branches = (
            table_rows(connection, "search_branches")
            if _table_exists(connection, "search_branches")
            else []
        )
        providers = (
            table_rows(connection, "provider_calls")
            if _table_exists(connection, "provider_calls")
            else []
        )
        finals = (
            table_rows(connection, "final_results")
            if _table_exists(connection, "final_results")
            else []
        )
        quality = (
            table_rows(connection, "search_quality_scores")
            if _table_exists(connection, "search_quality_scores")
            else []
        )
        embeddings = (
            table_rows(connection, "query_embeddings")
            if _table_exists(connection, "query_embeddings")
            else []
        )
    expected_queries = {record["query"] for record in batch_calls}
    runs = [row for row in runs if row["query"] in expected_queries]
    persisted_queries = {row["query"] for row in runs}
    if len(runs) != 5 or persisted_queries != expected_queries:
        failures.append(
            f"search_runs mismatch: rows={len(runs)} distinct_queries={len(persisted_queries)}"
        )
    if any(row.get("rewrite_enabled") is not True for row in runs):
        failures.append("not every search_runs row has rewrite_enabled=true")
    by_run = {row["run_key"]: row for row in runs}
    successful_queries = {
        record["query"] for record in batch_calls if record["status"] == "success"
    }
    for run_key, row in by_run.items():
        if row["query"] not in successful_queries:
            continue
        branch_count = sum(item["run_key"] == run_key for item in branches)
        final_count = sum(item["run_key"] == run_key for item in finals)
        provider_count = sum(item["run_key"] == run_key for item in providers)
        quality_count = sum(item["run_key"] == run_key for item in quality)
        if branch_count != row.get("branch_count"):
            failures.append(f"{run_key}: branch rows {branch_count} != {row.get('branch_count')}")
        if final_count != row.get("final_result_count"):
            failures.append(
                f"{run_key}: final rows {final_count} != {row.get('final_result_count')}"
            )
        if provider_count < 1 or quality_count != 1:
            failures.append(
                f"{run_key}: provider rows={provider_count}, quality rows={quality_count}"
            )
        payload = _json_object(row.get("payload_json"))
        dimension = _find_key(payload, "query_embedding_dim")
        if dimension == 786:
            matches = [item for item in embeddings if item["run_key"] == run_key]
            dimensions = [len(item.get("embedding") or []) for item in matches]
            if len(matches) != 1 or dimensions != [786]:
                failures.append(f"{run_key}: expected one 786D query embedding, got {dimensions}")
    if not logs_path.exists():
        failures.append("process_logs.duckdb missing")
        log_count = debug_count = 0
    else:
        with duckdb.connect(str(logs_path), read_only=True) as connection:
            log_count = connection.execute("SELECT count(*) FROM process_logs").fetchone()[0]
            debug_count = connection.execute(
                "SELECT count(*) FROM process_logs WHERE level = 'DEBUG'"
            ).fetchone()[0]
        if not log_count or not debug_count:
            failures.append(f"process logs insufficient: total={log_count}, debug={debug_count}")
    if not (run_dir / "server.stderr.log").exists():
        failures.append("server.stderr.log missing")
    return {
        "passed": not failures,
        "failures": failures,
        "counts": {"runs": len(runs), "logs": log_count, "debug_logs": debug_count},
    }


def extract_results(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw = record.get("raw_result") or {}
    structured = raw.get("structuredContent") or raw.get("structured_content")
    candidates: list[Any] = [structured]
    for block in raw.get("content") or []:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            try:
                candidates.append(json.loads(block["text"]))
            except json.JSONDecodeError:
                continue
    for candidate in candidates:
        if isinstance(candidate, dict) and isinstance(candidate.get("result"), dict):
            candidate = candidate["result"]
        if isinstance(candidate, dict) and isinstance(candidate.get("results"), list):
            return [item for item in candidate["results"] if isinstance(item, dict)]
    return []


def _quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * probability
    lower, upper = math.floor(index), math.ceil(index)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _latencies(records: list[dict[str, Any]]) -> dict[str, float | None]:
    values = [
        float(record["latency_ms"]) for record in records if record.get("latency_ms") is not None
    ]
    return {
        "min": min(values) if values else None,
        "mean": statistics.fmean(values) if values else None,
        "p50": _quantile(values, 0.50),
        "p90": _quantile(values, 0.90),
        "p95": _quantile(values, 0.95),
        "max": max(values) if values else None,
    }
