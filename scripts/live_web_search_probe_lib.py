from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

BASE_QUERIES = [
    "FastMCP middleware official documentation",
    "OpenTelemetry Python LoggingHandler OTLP logs Grafana Loki",
    "Loki OpenTelemetry service_name resource attributes",
    "Tempo TraceQL span:name resource.service.name examples",
    "SearXNG JSON search engine configuration engines keep_only",
    "Jina reranker v3 API request response schema",
    "MCP server progress notifications tool call session",
    "GitHub GraphQL discussions comments replies query examples",
    "Python asyncio TaskGroup cancellation exception handling docs",
    "React useEffectEvent official docs",
]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_command(root: Path) -> str:
    return str(root / ".venv" / "Scripts" / "kindly-web-search.exe")


def parse_providers(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    providers = [item.strip() for item in raw.split(",") if item.strip()]
    return providers or None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_call_result(result: Any) -> dict[str, Any]:
    if hasattr(result, "structured_content") and result.structured_content is not None:
        payload = result.structured_content
    elif hasattr(result, "data") and result.data is not None:
        payload = result.data
    elif hasattr(result, "content"):
        payload = normalize_content_blocks(result.content)
    else:
        payload = result
    normalized = payload if isinstance(payload, dict) else {"content": payload}
    if hasattr(result, "is_error"):
        normalized["_mcp_is_error"] = bool(result.is_error)
    if hasattr(result, "meta") and result.meta:
        normalized["_mcp_meta"] = result.meta
    return normalized


def normalize_content_blocks(content: Any) -> Any:
    if not isinstance(content, list):
        return str(content)
    texts = [getattr(item, "text", None) or str(item) for item in content]
    if len(texts) != 1:
        return {"content": texts}
    try:
        return json.loads(texts[0])
    except json.JSONDecodeError:
        return {"text": texts[0]}


def build_case(
    index: int,
    *,
    mode: str,
    run_id: str,
    providers: list[str] | None,
    query_override: str | None = None,
) -> dict[str, Any]:
    query = query_override or BASE_QUERIES[index % len(BASE_QUERIES)]
    goal = (
        f"Live MCP observability/load probe {run_id} call {index + 1}. "
        "Assess result quality, provider flow, latency, warnings, and telemetry correlation."
    )
    if providers is not None:
        return {"query": query, "research_goal": goal, "rewrite": mode != "literal", "providers": providers}
    if mode == "literal":
        return {"query": query, "research_goal": goal, "rewrite": False, "providers": ["searxng"]}
    pattern = index % 5
    if pattern == 0:
        return {"query": query, "research_goal": goal, "rewrite": False, "providers": ["searxng"]}
    if pattern == 1:
        return {"query": query, "research_goal": goal, "rewrite": True, "providers": ["searxng"]}
    if pattern == 2:
        return {"query": query, "research_goal": goal, "rewrite": False, "providers": ["searxng", "ddg"]}
    return {"query": query, "research_goal": goal, "rewrite": True, "providers": None}


def build_arguments(case: dict[str, Any], *, num_results: int) -> dict[str, Any]:
    arguments = {
        "query": case["query"],
        "research_goal": case["research_goal"],
        "num_results": num_results,
        "rewrite": case["rewrite"],
    }
    if case.get("providers") is not None:
        arguments["providers"] = case["providers"]
    return arguments


async def run_probe(args: Any) -> tuple[Path, Path]:
    root = repo_root()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id or uuid.uuid4().hex[:12]
    raw_path = output_dir / f"web_search_live_{run_id}.jsonl"
    summary_path = output_dir / f"web_search_live_{run_id}_summary.json"
    env = os.environ.copy()
    env.update({"PYTHONUNBUFFERED": "1", "FASTMCP_SHOW_SERVER_BANNER": "false"})
    transport = StdioTransport(
        command=args.command or default_command(root),
        args=args.command_args,
        env=env,
        cwd=args.cwd or str(root),
        log_file=output_dir / f"web_search_live_{run_id}_server.log",
    )
    records: list[dict[str, Any]] = []
    provider_override = parse_providers(args.providers)
    started = time.monotonic()
    async with Client(transport, timeout=args.timeout_seconds, init_timeout=30) as client:
        await client.list_tools()
        for index in range(args.count):
            await sleep_until(started + index * args.interval_seconds)
            case = build_case(
                index,
                mode=args.mode,
                run_id=run_id,
                providers=provider_override,
                query_override=getattr(args, "query", None),
            )
            record = await call_once(client, index=index, arguments=build_arguments(case, num_results=args.num_results))
            records.append(record)
            append_jsonl(raw_path, record)
            print_progress(record)
        linger_seconds = max(0.0, float(getattr(args, "linger_seconds", 0.0) or 0.0))
        if linger_seconds:
            await asyncio.sleep(linger_seconds)
    summary_path.write_text(
        json.dumps(analyze(records, run_id=run_id, raw_path=raw_path), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return raw_path, summary_path


async def sleep_until(monotonic_deadline: float) -> None:
    wait_seconds = monotonic_deadline - time.monotonic()
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)


async def call_once(client: Client, *, index: int, arguments: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = await client.call_tool("web_search", arguments=arguments, timeout=120, raise_on_error=False)
        response = normalize_call_result(result)
        error = None
    except Exception as exc:
        response = None
        error = {"type": type(exc).__name__, "message": str(exc)}
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    return {
        "index": index,
        "started_at": utc_now(),
        "duration_ms": duration_ms,
        "arguments": arguments,
        "ok": error is None and not bool((response or {}).get("_mcp_is_error")),
        "error": error,
        "response": response,
        "analysis": analyze_one(response, error, duration_ms),
    }


def analyze_one(response: dict[str, Any] | None, error: dict[str, Any] | None, duration_ms: float) -> dict[str, Any]:
    if error or not isinstance(response, dict):
        return {"duration_ms": duration_ms, "result_count": 0, "warning_count": 0}
    results = response.get("results") or []
    warnings = response.get("warnings") or []
    return {
        "duration_ms": duration_ms,
        "result_count": len(results) if isinstance(results, list) else 0,
        "warning_count": len(warnings) if isinstance(warnings, list) else 0,
        "providers_used": response.get("providers_used") or [],
        "has_more": (response.get("result_window") or {}).get("has_more"),
        "top_domain": domain_of(results[0].get("link", "")) if results else None,
    }


def domain_of(url: str) -> str:
    return url.split("://", 1)[-1].split("/", 1)[0].lower()


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return sorted(values)[round((pct / 100) * (len(values) - 1))]


def analyze(records: list[dict[str, Any]], *, run_id: str, raw_path: Path) -> dict[str, Any]:
    durations = [record["duration_ms"] for record in records]
    provider_counts: Counter[str] = Counter()
    domain_counts: Counter[str] = Counter()
    warning_types: Counter[str] = Counter()
    empty_results = 0
    for record in records:
        item = record.get("analysis") or {}
        provider_counts.update(item.get("providers_used") or [])
        if item.get("top_domain"):
            domain_counts[item["top_domain"]] += 1
        if item.get("result_count", 0) == 0:
            empty_results += 1
        for warning in (record.get("response") or {}).get("warnings") or []:
            warning_types[f"{warning.get('provider')}:{warning.get('error_type')}"] += 1
    successes = [record for record in records if record["ok"]]
    return {
        "run_id": run_id,
        "raw_path": str(raw_path),
        "call_count": len(records),
        "success_count": len(successes),
        "error_count": len(records) - len(successes),
        "empty_result_count": empty_results,
        "duration_ms": {
            "min": min(durations) if durations else None,
            "max": max(durations) if durations else None,
            "avg": round(statistics.mean(durations), 3) if durations else None,
            "p50": percentile(durations, 50),
            "p95": percentile(durations, 95),
        },
        "providers_used": dict(provider_counts.most_common()),
        "top_domains": dict(domain_counts.most_common(20)),
        "warning_types": dict(warning_types.most_common()),
    }


def print_progress(record: dict[str, Any]) -> None:
    item = record["analysis"]
    print(
        f"{record['index'] + 1:03d} {'ok' if record['ok'] else 'error'} "
        f"{item.get('duration_ms')}ms results={item.get('result_count')} "
        f"warnings={item.get('warning_count')} top={item.get('top_domain')}"
    )
