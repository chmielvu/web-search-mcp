"""Diagnostic: identify every asyncio task and its coroutine stack during a pipeline run.

Dumps task name, coroutine qualname, source file:line, and live stack frames
for every pending task at 1-second intervals. Exits when pipeline returns
(all tasks drained) or after 90 seconds.

Usage:
    python scripts/diag_task_inspector.py "FastMCP transports" 5
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

_t0 = time.monotonic()


def _task_dump(t: asyncio.Task) -> dict:
    """Extract everything useful from a task/coroutine."""
    info: dict = {"name": t.get_name(), "done": t.done(), "cancelled": t.cancelled()}
    coro = getattr(t, "_coro", None) or getattr(t, "get_coro", lambda: None)()
    if coro:
        info["coro_qualname"] = getattr(coro, "__qualname__", str(coro))
        frame = getattr(coro, "cr_frame", None)
        if frame:
            info["file"] = frame.f_code.co_filename
            info["func"] = frame.f_code.co_name
            info["line"] = frame.f_lineno
            # Extract source line
            try:
                src_lines = inspect.getsourcelines(frame)
                if src_lines:
                    offset, lines = src_lines[1], src_lines[0]
                    idx = frame.f_lineno - offset
                    if 0 <= idx < len(lines):
                        info["source"] = lines[idx].strip()
            except Exception:
                pass
            # Walk the frame chain
            frames = []
            f = frame
            while f is not None:
                frames.append(
                    {
                        "func": f.f_code.co_name,
                        "file": Path(f.f_code.co_filename).name,
                        "line": f.f_lineno,
                    }
                )
                f = f.f_back
            info["stack"] = frames
    return info


def _print_tasks() -> None:
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    elapsed = (time.monotonic() - _t0) * 1000.0
    if not tasks:
        print(f"[{elapsed:8.1f}ms] NO PENDING TASKS", file=sys.stderr, flush=True)
        return
    print(f"[{elapsed:8.1f}ms] {len(tasks)} pending tasks:", file=sys.stderr, flush=True)
    for t in tasks:
        info = _task_dump(t)
        print(
            f"  ── {info['name']} ({'done' if info['done'] else 'running'})",
            file=sys.stderr,
            flush=True,
        )
        if "coro_qualname" in info:
            print(f"      coro: {info['coro_qualname']}", file=sys.stderr, flush=True)
        if "func" in info:
            print(
                f"      at:  {info.get('file', '?')}:{info['line']} in {info['func']}",
                file=sys.stderr,
                flush=True,
            )
        if "source" in info:
            print(f"      src: {info['source']}", file=sys.stderr, flush=True)
        if "stack" in info:
            for s in info["stack"]:
                print(f"      ↳ {s['func']} ({s['file']}:{s['line']})", file=sys.stderr, flush=True)
    print("", file=sys.stderr, flush=True)


async def _inspector() -> None:
    """Print task details every 1s, more often right after pipeline returns."""
    while True:
        _print_tasks()
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if not tasks:
            return
        await asyncio.sleep(1.0)


async def main(query: str, num_results: int) -> None:
    import httpx

    from kindly_web_search_mcp_server.search.contracts import WebSearchRequest
    from kindly_web_search_mcp_server.search.service import execute_web_search

    inspector = asyncio.create_task(_inspector())

    print(
        f"[{'{:.1f}'.format(0)}ms] STARTING PIPELINE query={query!r} n={num_results}",
        file=sys.stderr,
        flush=True,
    )

    request = WebSearchRequest(
        query=query,
        research_goal="diagnostic pipeline probe",
        num_results=15,
        rewrite=True,
    )

    async with httpx.AsyncClient() as client:
        response, run = await execute_web_search(
            request,
            http_client=client,
            run_key=f"diag-{int(time.time())}",
            return_diagnostics=True,
        )
    elapsed = (time.monotonic() - _t0) * 1000.0
    print(
        f"[{elapsed:8.1f}ms] PIPELINE RETURNED results={len(response.results)}",
        file=sys.stderr,
        flush=True,
    )
    print(json.dumps({"data": response.model_dump()}, default=str, ensure_ascii=False))
    await inspector
    print(
        f"[{(time.monotonic() - _t0) * 1000:.1f}ms] ALL TASKS DRAINED",
        file=sys.stderr,
        flush=True,
    )


if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "FastMCP transports"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    logging.basicConfig(
        level=logging.WARNING,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        asyncio.run(main(query, n))
    except KeyboardInterrupt:
        print("\n[INTERRUPTED]", file=sys.stderr)
    except asyncio.TimeoutError:
        print("\n[TIMEOUT]", file=sys.stderr)
