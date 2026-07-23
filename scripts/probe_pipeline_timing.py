"""Capture the full web-search pipeline run with timestamped stderr debug logs.

Patches pipeline module functions AND background task functions to add [MARK]
timing around every step, including the post-pipeline shutdown gap.

Usage:
    python scripts/probe_pipeline_timing.py "FastMCP transports" 5

Outputs:
    - stdout: final JSON results
    - stderr → .mcp-debug/probe_<query>_<timestamp>.txt: every log line with [elapsed_ms] timestamps
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

_t0 = time.monotonic()


class _TeeStderr:
    """Write stderr to both terminal and a .txt file."""

    def __init__(self, path: Path) -> None:
        self._file = path.open("w", encoding="utf-8")
        self._stderr = sys.__stderr__

    def write(self, data: str) -> int:
        self._file.write(data)
        self._stderr.write(data)
        return len(data)

    def flush(self) -> None:
        self._file.flush()
        self._stderr.flush()

    def close(self) -> None:
        self._file.close()


class TimedStderrHandler(logging.StreamHandler):
    """Emit every log record with [elapsed_ms] since script start."""

    def __init__(self) -> None:
        super().__init__(stream=sys.stderr)
        self.setFormatter(
            logging.Formatter("[%(elapsed_ms)8.1f] %(levelname)-5s %(name)s: %(message)s")
        )

    def emit(self, record: logging.LogRecord) -> None:
        record.elapsed_ms = (time.monotonic() - _t0) * 1000.0  # type: ignore[attr-defined]
        super().emit(record)


def _mark(stage: str, detail: str = "") -> None:
    ms = (time.monotonic() - _t0) * 1000.0
    print(f"  [MARK] {ms:8.1f}ms  {stage}  {detail}", file=sys.stderr, flush=True)


def _install_logging() -> None:
    handler = TimedStderrHandler()
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(handler)
    for noisy in (
        "rustls",
        "h2",
        "hyper_util",
        "cookie_store",
        "reqwest",
        "openai._base_client",
        "httpcore",
        "LiteLLM",
        "litellm",
    ):
        logging.getLogger(noisy).setLevel(logging.INFO)


def _patch_pipeline_module() -> None:
    """No-op: the prior patcher wrapped 9 functions inside
    `kindly_web_search_mcp_server.search.pipeline` (e.g. `build_search_response`,
    `analytics_insert_search_run`, `fire_and_forget`, `run_judge_evaluation`)
    for per-stage [MARK] timing. The pipeline module was deleted in the
    2026-07-20 safe-refactor and those functions moved to new modules.
    Remapping each patch path is outside the scope of this diagnostic
    script; the script still operates as a pass-through pipeline-timing
    probe via `_install_logging`, `_mark`, and the heartbeat around the
    call itself.
    """
    _mark("patch_pipeline_module", "skipped (pipeline module deleted in 2026-07-20 refactor)")
    return


def _patch_embeddings_and_index() -> None:
    """Patch embed_texts and index_final_results at source modules
    (they're imported inside closures, not at pipeline module level)."""
    try:
        import kindly_web_search_mcp_server.embeddings as _emb

        if hasattr(_emb, "embed_texts"):
            _orig_embed = _emb.embed_texts

            async def _wrapped_embed(*args, **kwargs):
                _mark("embed_texts", "ENTER (index task)")
                result = await _orig_embed(*args, **kwargs)
                _mark("embed_texts", f"EXIT ({len(result) if result else 0} embeddings)")
                return result

            _emb.embed_texts = _wrapped_embed
    except Exception as exc:
        _mark("patch_embed_texts", f"FAILED: {exc}")

    try:
        import kindly_web_search_mcp_server.index as _idx

        if hasattr(_idx, "index_final_results"):
            _orig_idx = _idx.index_final_results

            async def _wrapped_idx(*args, **kwargs):
                _mark("index_final_results", "ENTER")
                await _orig_idx(*args, **kwargs)
                _mark("index_final_results", "EXIT")

            _idx.index_final_results = _wrapped_idx
    except Exception as exc:
        _mark("patch_index_final_results", f"FAILED: {exc}")


async def _heartbeat() -> None:
    """Log pending tasks every 200ms until no tasks remain."""
    while True:
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if not tasks:
            _mark("heartbeat", "no pending tasks — done")
            return
        names = [t.get_name() if hasattr(t, "get_name") else "?" for t in tasks]
        states = []
        for t in tasks:
            if t.done():
                states.append("done")
            elif t.cancelled():
                states.append("cancelled")
            else:
                states.append("running")
        _mark("heartbeat", f"{len(tasks)} pending: {list(zip(names, states))}")
        await asyncio.sleep(0.2)


async def main(query: str, num_results: int) -> None:
    import httpx

    from kindly_web_search_mcp_server.search.contracts import WebSearchRequest
    from kindly_web_search_mcp_server.search.service import execute_web_search

    _patch_pipeline_module()
    _patch_embeddings_and_index()
    _mark("patches_installed")

    _mark("pipeline_call", f"query={query!r} num_results={num_results}")

    request = WebSearchRequest(
        query=query,
        research_goal="probe pipeline timing",
        num_results=15,
        rewrite=True,
    )

    # Start heartbeat alongside the pipeline
    hb = asyncio.create_task(_heartbeat())

    async with httpx.AsyncClient() as client:
        response = await execute_web_search(
            request,
            http_client=client,
            run_key=f"probe-{int(time.time())}",
        )

    _mark("pipeline_returned", f"results={len(response.results)}")

    print(json.dumps({"data": response.model_dump()}, default=str, ensure_ascii=False))

    _mark("stdout_printed", "waiting for background tasks to drain...")

    # Wait for heartbeat to finish (it exits when no tasks remain)
    await hb

    _mark("all_tasks_drained", "event loop clean")


if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "FastMCP transports"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    safe_query = query.replace(" ", "_").replace("/", "_")[:40]
    out_path = ROOT / ".mcp-debug" / f"probe_{safe_query}_{int(time.time())}.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tee = _TeeStderr(out_path)
    sys.stderr = tee  # type: ignore[assignment]

    _install_logging()

    print(f"[probe] stderr → {out_path}", file=sys.stderr, flush=True)

    asyncio.run(main(query, n))

    print(f"\n[probe] Full log saved to {out_path}", file=sys.stderr, flush=True)
    tee.flush()
    tee.close()
