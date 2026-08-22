from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import duckdb

from kindly_web_search_mcp_server.search.normalize import canonicalize_url
from live_web_search_quality_integrity import analytics_integrity
from live_web_search_quality_support import (
    EXPORT_TABLES,
    _find_key,
    _json_object,
    _latencies,
    _quantile,
    _table_exists,
    dump_json,
    extract_results,
    json_default,
    read_jsonl,
    table_rows,
)


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, default=json_default) + "\n")


def _group(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("run_key"))].append(row)
    return grouped


def export_databases(run_dir: Path) -> dict[str, list[dict[str, Any]]]:
    exported: dict[str, list[dict[str, Any]]] = {}
    analytics_path = run_dir / "analytics.duckdb"
    if analytics_path.exists():
        with duckdb.connect(str(analytics_path), read_only=True) as connection:
            for table in EXPORT_TABLES:
                rows = table_rows(connection, table) if _table_exists(connection, table) else []
                exported[table] = rows
                _write_rows(run_dir / "analytics" / f"{table}.jsonl", rows)
            coverage: list[dict[str, Any]] = []
            for table in ("query_embeddings", "candidate_embeddings"):
                if not _table_exists(connection, table):
                    continue
                columns = "run_key, model_id, array_length(embedding) AS vector_dimension"
                if table == "candidate_embeddings":
                    columns = "run_key, link, model_id, array_length(embedding) AS vector_dimension"
                result = connection.execute(f"SELECT {columns} FROM {table} ORDER BY recorded_at")
                names = [item[0] for item in connection.description]
                coverage.extend(
                    {"embedding_table": table, **dict(zip(names, row, strict=True))}
                    for row in result.fetchall()
                )
            exported["embedding_coverage"] = coverage
            _write_rows(run_dir / "analytics" / "embedding_coverage.jsonl", coverage)
    logs_path = run_dir / "process_logs.duckdb"
    logs: list[dict[str, Any]] = []
    if logs_path.exists():
        with duckdb.connect(str(logs_path), read_only=True) as connection:
            if _table_exists(connection, "process_logs"):
                logs = table_rows(connection, "process_logs")
    exported["process_logs"] = logs
    _write_rows(run_dir / "process_logs.jsonl", logs)
    return exported


def _structured(record: dict[str, Any]) -> bool:
    raw = record.get("raw_result") or {}
    return (raw.get("structuredContent") or raw.get("structured_content")) is not None


def _url_metrics(calls: list[dict[str, Any]]) -> tuple[dict[str, Any], Counter[str]]:
    total = duplicates = invalid = missing_title = missing_snippet = https = 0
    cross_seen: set[str] = set()
    cross_duplicates = 0
    domains: Counter[str] = Counter()
    for call in calls:
        local_seen: set[str] = set()
        for result in extract_results(call):
            total += 1
            title = str(result.get("title") or "").strip()
            snippet = str(result.get("snippet") or result.get("description") or "").strip()
            missing_title += not bool(title)
            missing_snippet += not bool(snippet)
            link = str(result.get("url") or result.get("link") or "").strip()
            try:
                normalized = canonicalize_url(link)
                parsed = urlsplit(normalized)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    raise ValueError("invalid URL")
            except Exception:
                invalid += 1
                continue
            duplicates += normalized in local_seen
            cross_duplicates += normalized in cross_seen
            local_seen.add(normalized)
            cross_seen.add(normalized)
            domains[parsed.hostname.lower()] += 1
            https += parsed.scheme == "https"
    valid = total - invalid
    return {
        "result_occurrences": total,
        "within_query_duplicate_rate": duplicates / total if total else None,
        "cross_query_duplicate_rate": cross_duplicates / total if total else None,
        "invalid_url_rate": invalid / total if total else None,
        "missing_title_rate": missing_title / total if total else None,
        "missing_snippet_rate": missing_snippet / total if total else None,
        "https_rate": https / valid if valid else None,
        "top_domain_concentration": max(domains.values()) / valid if valid and domains else None,
        "unique_domains": len(domains),
    }, domains


def _provider_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_provider: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_provider[str(row.get("provider"))].append(row)
    return {
        provider: {
            "calls": len(items),
            "statuses": dict(Counter(str(item.get("status")) for item in items)),
            "latency_p50_ms": _quantile(
                [float(item["latency_ms"]) for item in items if item.get("latency_ms") is not None],
                0.5,
            ),
            "latency_p95_ms": _quantile(
                [float(item["latency_ms"]) for item in items if item.get("latency_ms") is not None],
                0.95,
            ),
        }
        for provider, items in sorted(by_provider.items())
    }


def _family_summary(calls: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for family in ("N", "S"):
        items = [record for record in calls if str(record["id"]).startswith(family)]
        successes = [record for record in items if record["status"] == "success"]
        counts = [len(extract_results(record)) for record in successes]
        output[family] = {
            "attempts": len(items),
            "statuses": dict(Counter(record["status"] for record in items)),
            "latency_ms": _latencies(items),
            "mean_results": statistics.fmean(counts) if counts else None,
        }
    return output


def build_outputs(
    run_dir: Path, corpus: list[dict[str, Any]], gate: dict[str, Any]
) -> dict[str, Any]:
    calls = read_jsonl(run_dir / "calls.jsonl")
    progress = read_jsonl(run_dir / "progress.jsonl")
    exported = export_databases(run_dir)
    runs = exported.get("search_runs", [])
    run_by_query = {str(row.get("query")): row for row in runs}
    grouped = {table: _group(rows) for table, rows in exported.items() if table != "process_logs"}
    quality_rows: list[dict[str, Any]] = []
    for call in calls:
        run = run_by_query.get(call["query"])
        run_key = str(run.get("run_key")) if run else None
        row = {
            **{
                key: call.get(key)
                for key in (
                    "id",
                    "batch",
                    "topic",
                    "query",
                    "research_goal",
                    "status",
                    "latency_ms",
                )
            },
            "run": run,
            "branches": grouped.get("search_branches", {}).get(run_key, []),
            "provider_calls": grouped.get("provider_calls", {}).get(run_key, []),
            "final_results": grouped.get("final_results", {}).get(run_key, []),
            "rerank_stages": grouped.get("rerank_stages", {}).get(run_key, []),
            "quality": grouped.get("search_quality_scores", {}).get(run_key, []),
            "mcp_results": extract_results(call),
        }
        quality_rows.append(row)
    _write_rows(run_dir / "quality_by_query.jsonl", quality_rows)
    statuses = Counter(record["status"] for record in calls)
    successful = [record for record in calls if record["status"] == "success"]
    progress_ids = {record.get("id") for record in progress}
    hygiene, domains = _url_metrics(calls)
    provider_rows = exported.get("provider_calls", [])
    rerank_rows = exported.get("rerank_stages", [])
    embedding_rows = exported.get("embedding_coverage", [])
    expected_embedding_runs = {
        row["run_key"]
        for row in runs
        if _find_key(_json_object(row.get("payload_json")), "query_embedding_dim") == 786
    }
    actual_embedding_runs = {
        row["run_key"]
        for row in embedding_rows
        if row.get("embedding_table") == "query_embeddings" and row.get("vector_dimension") == 786
    }
    selected = Counter(
        provider for row in runs for provider in (row.get("selected_providers") or [])
    )
    rewrite_statuses = Counter("error" if row.get("rewrite_error") else "success" for row in runs)
    rewrite_latencies = [
        float(row["rewrite_latency_ms"])
        for row in runs
        if row.get("rewrite_latency_ms") is not None
    ]
    result_counts = [len(extract_results(record)) for record in successful]
    summary = {
        "attempts": len(calls),
        "terminal_statuses": dict(statuses),
        "terminal_status_rates": {key: value / len(calls) for key, value in statuses.items()}
        if calls
        else {},
        "latency_ms": _latencies(calls),
        "progress_coverage": len(progress_ids) / len(calls) if calls else None,
        "structured_content_coverage": sum(_structured(record) for record in successful)
        / len(successful)
        if successful
        else None,
        "result_count_distribution": {
            "min": min(result_counts) if result_counts else None,
            "mean": statistics.fmean(result_counts) if result_counts else None,
            "p50": _quantile([float(value) for value in result_counts], 0.5),
            "max": max(result_counts) if result_counts else None,
        },
        "result_hygiene": hygiene,
        "top_domains": domains.most_common(20),
        "selected_provider_frequency": dict(selected),
        "provider_calls": _provider_summary(provider_rows),
        "branch_count_distribution": dict(Counter(row.get("branch_count") for row in runs)),
        "merged_to_final_compression_mean": statistics.fmean(
            1 - row["final_result_count"] / row["merged_count"]
            for row in runs
            if row.get("merged_count") and row.get("final_result_count") is not None
        )
        if any(row.get("merged_count") for row in runs)
        else None,
        "rewrite": {
            "statuses": dict(rewrite_statuses),
            "latency_p50_ms": _quantile(rewrite_latencies, 0.5),
            "latency_p95_ms": _quantile(rewrite_latencies, 0.95),
        },
        "rerank_stages": {
            "statuses": dict(Counter(str(row.get("status")) for row in rerank_rows)),
            "by_stage": dict(Counter(str(row.get("stage")) for row in rerank_rows)),
        },
        "query_embedding_coverage": len(actual_embedding_runs & expected_embedding_runs)
        / len(expected_embedding_runs)
        if expected_embedding_runs
        else None,
        "candidate_embedding_rows": sum(
            row.get("embedding_table") == "candidate_embeddings" for row in embedding_rows
        ),
        "analytics_gate": gate,
        "analytics_integrity": analytics_integrity(calls, exported),
        "topic_families": _family_summary(calls),
    }
    dump_json(run_dir / "summary.json", summary)
    return summary
