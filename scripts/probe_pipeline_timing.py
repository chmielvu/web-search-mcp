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
    """Patch pipeline module namespace so timing wraps the real calls."""
    import kindly_web_search_mcp_server.search.pipeline as _pipe

    # --- build_search_response ---
    _orig_build = _pipe.build_search_response

    def _wrapped_build(*args, **kwargs):
        _mark("build_search_response", "ENTER")
        result = _orig_build(*args, **kwargs)
        _mark("build_search_response", "EXIT")
        return result

    _pipe.build_search_response = _wrapped_build

    # --- record_search_request ---
    _orig_record = _pipe.record_search_request

    def _wrapped_record(*args, **kwargs):
        _mark("record_search_request", "ENTER")
        _orig_record(*args, **kwargs)
        _mark("record_search_request", "EXIT")

    _pipe.record_search_request = _wrapped_record

    # --- append_query_outcome_record ---
    _orig_append = _pipe.append_query_outcome_record

    async def _wrapped_append(*args, **kwargs):
        _mark("append_query_outcome_record", "ENTER")
        await _orig_append(*args, **kwargs)
        _mark("append_query_outcome_record", "EXIT")

    _pipe.append_query_outcome_record = _wrapped_append

    # --- analytics_insert_search_run ---
    _orig_insert_run = _pipe.analytics_insert_search_run

    def _wrapped_insert_run(*args, **kwargs):
        _mark("analytics_insert_search_run", "ENTER")
        _orig_insert_run(*args, **kwargs)
        _mark("analytics_insert_search_run", "EXIT")

    _pipe.analytics_insert_search_run = _wrapped_insert_run

    # --- analytics_insert_final_results ---
    _orig_insert_final = _pipe.analytics_insert_final_results

    def _wrapped_insert_final(*args, **kwargs):
        _mark("analytics_insert_final_results", "ENTER")
        _orig_insert_final(*args, **kwargs)
        _mark("analytics_insert_final_results", "EXIT")

    _pipe.analytics_insert_final_results = _wrapped_insert_final

    # --- analytics_insert_pipeline_heartbeat ---
    _orig_hb = _pipe.analytics_insert_pipeline_heartbeat

    def _wrapped_hb(*args, **kwargs):
        _mark("analytics_insert_pipeline_heartbeat", "ENTER")
        _orig_hb(*args, **kwargs)
        _mark("analytics_insert_pipeline_heartbeat", "EXIT")

    _pipe.analytics_insert_pipeline_heartbeat = _wrapped_hb

    # --- fire_and_forget ---
    _orig_faf = _pipe.fire_and_forget

    def _wrapped_faf(coro, *, name=None):
        _mark("fire_and_forget", f"spawn name={name}")
        return _orig_faf(coro, name=name)

    _pipe.fire_and_forget = _wrapped_faf

    # --- compute_search_quality (called by _compute_quality closure) ---
    _orig_quality = _pipe.compute_search_quality

    def _wrapped_quality(*args, **kwargs):
        _mark("compute_search_quality", "ENTER")
        _orig_quality(*args, **kwargs)
        _mark("compute_search_quality", "EXIT")

    _pipe.compute_search_quality = _wrapped_quality

    # --- run_judge_evaluation (called by fire_and_forget) ---
    _orig_judge = _pipe.run_judge_evaluation

    async def _wrapped_judge(*args, **kwargs):
        _mark("run_judge_evaluation", "ENTER")
        await _orig_judge(*args, **kwargs)
        _mark("run_judge_evaluation", "EXIT")

    _pipe.run_judge_evaluation = _wrapped_judge


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
    from kindly_web_search_mcp_server.search.pipeline import run_search_pipeline
    from kindly_web_search_mcp_server.search.options import SearchOptions
    from kindly_web_search_mcp_server.utils.diagnostics import Diagnostics

    _patch_pipeline_module()
    _patch_embeddings_and_index()
    _mark("patches_installed")

    _mark("pipeline_call", f"query={query!r} num_results={num_results}")

    # Start heartbeat alongside the pipeline
    hb = asyncio.create_task(_heartbeat())

    response = await run_search_pipeline(
        query=query,
        num_results=num_results,
        rewrite=True,
        diagnostics=Diagnostics(request_id="probe", enabled=False),
        research_goal=None,
        search_options=SearchOptions(),
        session_id=None,
        tool_call_id=None,
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
