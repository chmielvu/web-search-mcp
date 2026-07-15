from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from live_web_search_quality_pandas import export_pandas
from live_web_search_quality_report import build_outputs
from live_web_search_quality_state import (
    CORPUS_PATH,
    DEFAULT_OUTPUT_ROOT,
    FIXED_PARAMETERS,
    ROOT,
    base_manifest,
    create_run_dir,
    load_resume_manifest,
    refresh_manifest,
    server_environment,
    utc_now,
    validate_tool_schema,
)
from live_web_search_quality_support import (
    analytics_gate,
    append_jsonl,
    dump_json,
    load_corpus,
    read_jsonl,
    wait_for_databases,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the 50-call live web-search quality campaign")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--inter-batch-delay-seconds", type=float, default=15)
    parser.add_argument("--call-timeout-seconds", type=float, default=180)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 5:
        parser.error("--batch-size must be between 1 and 5")
    if args.call_timeout_seconds <= 0 or args.inter_batch_delay_seconds < 0:
        parser.error("timeouts must be positive and delays non-negative")
    return args


class Campaign:
    def __init__(
        self,
        run_dir: Path,
        cases: list[dict[str, Any]],
        manifest: dict[str, Any],
        args: argparse.Namespace,
        uv_executable: str,
    ) -> None:
        self.run_dir = run_dir
        self.cases = cases
        self.manifest = manifest
        self.args = args
        self.uv_executable = uv_executable
        self.calls_lock = asyncio.Lock()
        self.progress_lock = asyncio.Lock()

    def transport(self) -> StdioTransport:
        return StdioTransport(
            command=self.uv_executable,
            args=["--directory", str(ROOT), "run", "web-search-mcp", "--transport", "stdio"],
            cwd=str(ROOT),
            env=server_environment(self.run_dir),
            keep_alive=True,
            log_file=self.run_dir / "server.stderr.log",
        )

    async def call_case(self, client: Client, case: dict[str, Any]) -> None:
        started_at = utc_now()
        started = time.monotonic()

        async def progress_handler(
            progress: float, total: float | None, message: str | None
        ) -> None:
            payload = {
                "recorded_at": utc_now(),
                "id": case["id"],
                "batch": case["batch"],
                "progress": progress,
                "total": total,
                "message": message,
            }
            async with self.progress_lock:
                append_jsonl(self.run_dir / "progress.jsonl", payload)

        raw_result: dict[str, Any] | None = None
        exception: dict[str, str] | None = None
        status = "transport_error"
        try:
            result = await client.call_tool_mcp(
                "web_search",
                {
                    "query": case["query"],
                    "research_goal": case["research_goal"],
                    **FIXED_PARAMETERS,
                },
                timeout=self.args.call_timeout_seconds,
                progress_handler=progress_handler,
            )
            raw_result = result.model_dump(mode="json", by_alias=True)
            status = "tool_error" if result.isError else "success"
        except (TimeoutError, asyncio.TimeoutError) as exc:
            status = "timeout"
            exception = {"class": type(exc).__name__, "message": str(exc)}
        except Exception as exc:
            message = str(exc)
            if "timed out while waiting for response" in message.lower():
                status = "timeout"
            exception = {"class": type(exc).__name__, "message": message}
        record = {
            **case,
            "started_at": started_at,
            "ended_at": utc_now(),
            "latency_ms": (time.monotonic() - started) * 1000,
            "status": status,
            "raw_result": raw_result,
            "exception": exception,
        }
        async with self.calls_lock:
            append_jsonl(self.run_dir / "calls.jsonl", record)

    async def run_batches(self, batches: list[int]) -> None:
        if not batches:
            return
        async with Client(self.transport()) as client:
            schema = validate_tool_schema(await client.list_tools())
            if self.manifest.get("tool_schema") is None:
                self.manifest["tool_schema"] = schema
                dump_json(self.run_dir / "manifest.json", self.manifest)
            for index, batch in enumerate(batches):
                consumed = {record["id"] for record in read_jsonl(self.run_dir / "calls.jsonl")}
                pending = [
                    case
                    for case in self.cases
                    if case["batch"] == batch and case["id"] not in consumed
                ]
                for offset in range(0, len(pending), self.args.batch_size):
                    chunk = pending[offset : offset + self.args.batch_size]
                    async with asyncio.TaskGroup() as group:
                        for case in chunk:
                            group.create_task(self.call_case(client, case))
                refresh_manifest(self.run_dir, self.manifest)
                if index < len(batches) - 1:
                    await asyncio.sleep(self.args.inter_batch_delay_seconds)


async def execute(args: argparse.Namespace) -> int:
    cases, corpus_hash = load_corpus(CORPUS_PATH)
    preview = {
        "case_count": len(cases),
        "batches": dict(
            sorted(
                {
                    batch: sum(case["batch"] == batch for case in cases) for batch in range(1, 11)
                }.items()
            )
        ),
        "fixed_parameters": FIXED_PARAMETERS,
    }
    if args.dry_run:
        print(json.dumps(preview, indent=2))
        return 0
    uv_executable = shutil.which("uv")
    if not uv_executable:
        raise RuntimeError("uv executable is not resolvable")
    run_dir = args.resume.resolve() if args.resume else create_run_dir(args.output_root)
    manifest = (
        load_resume_manifest(run_dir, corpus_hash, args)
        if args.resume
        else base_manifest(run_dir, corpus_hash, args, uv_executable)
    )
    dump_json(run_dir / "manifest.json", manifest)
    print(f"RUN_DIR={run_dir}", flush=True)
    campaign = Campaign(run_dir, cases, manifest, args, uv_executable)
    exit_code = 1
    try:
        calls = refresh_manifest(run_dir, manifest)
        consumed_batches = {record["batch"] for record in calls}
        if 1 not in consumed_batches or sum(record["batch"] == 1 for record in calls) < 5:
            await campaign.run_batches([1])
        await wait_for_databases(run_dir)
        batch_one = [
            record for record in read_jsonl(run_dir / "calls.jsonl") if record["batch"] == 1
        ]
        gate = analytics_gate(run_dir, batch_one)
        previous_gate = manifest.get("analytics_gate")
        if previous_gate and previous_gate != gate:
            manifest.setdefault("analytics_gate_history", []).append(
                {"recorded_at": utc_now(), **previous_gate}
            )
        manifest["analytics_gate"] = gate
        dump_json(run_dir / "manifest.json", manifest)
        if not gate["passed"]:
            raise RuntimeError("Batch 1 analytics gate failed: " + "; ".join(gate["failures"]))
        remaining = [batch for batch in range(2, 11) if batch not in manifest["completed_batches"]]
        await campaign.run_batches(remaining)
        await wait_for_databases(run_dir)
        calls = refresh_manifest(run_dir, manifest)
        if len(calls) != 50:
            raise RuntimeError(f"campaign ended with {len(calls)} attempts instead of 50")
        manifest["campaign_status"] = "completed"
        exit_code = 0
    except Exception as exc:
        manifest["campaign_status"] = "aborted"
        manifest["abort_reason"] = f"{type(exc).__name__}: {exc}"
        print(manifest["abort_reason"], file=sys.stderr)
    finally:
        manifest["ended_at"] = utc_now()
        refresh_manifest(run_dir, manifest)
        try:
            build_outputs(
                run_dir,
                cases,
                manifest.get("analytics_gate") or {"passed": False, "failures": ["gate not run"]},
            )
            export_pandas(run_dir)
        except Exception as exc:
            manifest["campaign_status"] = "aborted"
            manifest["abort_reason"] = f"report export failed: {type(exc).__name__}: {exc}"
            dump_json(run_dir / "manifest.json", manifest)
            print(manifest["abort_reason"], file=sys.stderr)
            exit_code = 1
    print(run_dir)
    return exit_code


def main() -> int:
    return asyncio.run(execute(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
