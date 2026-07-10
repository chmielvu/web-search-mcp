"""Debug MCP stdio startup for web-search-mcp.

Spawns the server as a child, captures both stdout and stderr to disk, sends an
initialize frame on stdin, and tears the server down after a configurable
timeout. Designed to surface the *real* reason the MCP host reports
"Transport closed": silent crashes, slow imports, or buffered logs.

Logs are written under `.mcp-debug/` at the repo root (gitignored).

Examples:

    python scripts/debug_mcp_stdio.py
    python scripts/debug_mcp_stdio.py --timeout 120
    python scripts/debug_mcp_stdio.py --skip-init
    python scripts/debug_mcp_stdio.py --skip-spawn
"""

from __future__ import annotations

import argparse
import os
import queue
import subprocess
import sys
import threading
import datetime
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = REPO_ROOT / ".mcp-debug"
LOG_DIR.mkdir(exist_ok=True)

STDOUT_PATH = LOG_DIR / "server.stdout.log"
STDERR_PATH = LOG_DIR / "server.stderr.log"
WRAPPER_PATH = LOG_DIR / "wrapper.log"

# Truncate prior logs so each run starts clean. Project-local only.
for _p in (STDOUT_PATH, STDERR_PATH, WRAPPER_PATH):
    _p.write_bytes(b"")


def _build_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "LOG_LEVEL": "DEBUG",
            "LOG_FORMAT": "full",
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": str(REPO_ROOT / "src"),
            "FASTMCP_LOG_LEVEL": "DEBUG",
            "FASTMCP_BANNER": "false",
            "FASTMCP_SHOW_SERVER_BANNER": "false",
        }
    )
    return env


def _reader(stream: object, sink: "queue.Queue[bytes]") -> None:
    try:
        for line in iter(stream.readline, b""):  # type: ignore[attr-defined]
            sink.put(line)
    except Exception as exc:  # pragma: no cover
        sys.stderr.write(f"<reader error: {exc!r}>\n")


def _import_only() -> int:
    """Reproduce just the import cost without mcp.run()."""
    proc = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            "import time, sys, traceback\n"
            "t = time.perf_counter()\n"
            "sys.stderr.write('IMPORT_BEGIN\\n'); sys.stderr.flush()\n"
            "try:\n"
            "  import kindly_web_search_mcp_server.server\n"
            "  sys.stderr.write(f'IMPORT_OK dur={time.perf_counter()-t:.1f}s\\n'); sys.stderr.flush()\n"
            "except BaseException:\n"
            "  sys.stderr.write('IMPORT_CRASH:\\n')\n"
            "  traceback.print_exc(file=sys.stderr)\n"
            "  sys.stderr.flush()\n"
            "time.sleep(0.5)\n",
        ],
        cwd=str(REPO_ROOT),
        env=_build_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    assert proc.stderr is not None
    print("[import_only] watching import...", flush=True)
    for raw in iter(proc.stderr.readline, b""):  # type: ignore[attr-defined]
        sys.stdout.write(raw.decode("utf-8", "replace"))
        sys.stdout.flush()
    return proc.wait()


def _spawn_server(timeout: float, skip_init: bool) -> int:
    """Spawn the server, capture every byte, send initialize, wait, kill."""
    stdout_handle = open(STDOUT_PATH, "wb", 0)
    stderr_handle = open(STDERR_PATH, "wb", 0)

    server_cmd = [
        sys.executable,
        "-u",
        "-m",
        "kindly_web_search_mcp_server.server",
        "--transport",
        "stdio",
    ]

    print(f"[spawn] cmd={server_cmd} cwd={REPO_ROOT}", flush=True)
    proc = subprocess.Popen(
        server_cmd,
        cwd=str(REPO_ROOT),
        env=_build_env(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    print(f"[spawn] pid={proc.pid}", flush=True)

    stdout_q: "queue.Queue[bytes]" = queue.Queue()
    stderr_q: "queue.Queue[bytes]" = queue.Queue()
    threading.Thread(target=_reader, args=(proc.stdout, stdout_q), daemon=True).start()
    threading.Thread(target=_reader, args=(proc.stderr, stderr_q), daemon=True).start()

    def tee(q: "queue.Queue[bytes]", handle, label: str) -> None:
        while True:
            try:
                line = q.get(timeout=timeout + 5)
            except queue.Empty:
                return
            handle.write(line)
            handle.flush()
            sys.stdout.write(f"[{label}] {line.decode('utf-8', 'replace')}")
            sys.stdout.flush()

    threading.Thread(target=tee, args=(stdout_q, stdout_handle, "STDOUT"), daemon=True).start()
    threading.Thread(target=tee, args=(stderr_q, stderr_handle, "STDERR"), daemon=True).start()

    if not skip_init:
        try:
            init_line = (
                b'{"jsonrpc":"2.0","id":1,"method":"initialize",'
                b'"params":{"protocolVersion":"2024-11-05",'
                b'"capabilities":{},"clientInfo":{"name":"debug-script","version":"0"}}}\n'
            )
            time.sleep(0.5)
            assert proc.stdin is not None
            proc.stdin.write(init_line)
            proc.stdin.flush()
            print("[spawn] initialize sent", flush=True)
        except Exception as exc:
            print(f"[spawn] stdin write failed: {exc!r}", flush=True)

    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        code = proc.poll()
        if code is not None:
            print(f"[spawn] process exited code={code}", flush=True)
            time.sleep(1.0)
            break
        time.sleep(0.5)

    if proc.poll() is None:
        print(f"[spawn] still alive after {timeout}s; killing", flush=True)
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    stdout_handle.close()
    stderr_handle.close()
    return proc.returncode if proc.returncode is not None else -1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--timeout", type=float, default=180.0,
                    help="Seconds to wait before killing the server (default 180).")
    ap.add_argument("--skip-init", action="store_true",
                    help="Don't send initialize on stdin.")
    ap.add_argument("--skip-spawn", action="store_true",
                    help="Only run the import-cost probe, do not spawn the server.")
    args = ap.parse_args()

    print(f"[main] logs: {LOG_DIR}", flush=True)

    with open(WRAPPER_PATH, "w", encoding="utf-8") as log:
        log.write(f"# debug_mcp_stdio start {datetime.datetime.now().isoformat()}\n")
        log.write(f"# timeout={args.timeout} skip_init={args.skip_init} skip_spawn={args.skip_spawn}\n")
        log.flush()

        if args.skip_spawn:
            log.write("\n## import_only mode\n")
            log.flush()
            code = _import_only()
            log.write(f"\n## import_only exit={code}\n")
            return code

        log.write("\n## spawn mode\n")
        log.flush()
        code = _spawn_server(timeout=args.timeout, skip_init=args.skip_init)
        log.write(f"\n## spawn exit={code}\n")

        log.write("\n## captured stderr (tail 200 lines)\n")
        if STDERR_PATH.exists():
            lines = STDERR_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
            log.write("\n".join(lines[-200:]))
            log.write("\n")
        log.write("\n## captured stdout (tail 60 lines)\n")
        if STDOUT_PATH.exists():
            lines = STDOUT_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
            log.write("\n".join(lines[-60:]))
            log.write("\n")

    print(f"[main] wrapper summary written to {WRAPPER_PATH}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
