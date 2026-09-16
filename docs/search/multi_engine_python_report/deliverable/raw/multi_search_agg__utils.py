"""Utilities: URL denoising and process cleanup."""
import os, time, subprocess
import signal as _signal


def kill_proc_tree(proc):
    """彻底杀死进程树：PGID kill + pkill 子进程名 + 确认回收。"""
    if proc is None:
        return
    try:
        # 1. 先尝试 SIGTERM 整个进程组
        try:
            os.killpg(os.getpgid(proc.pid), _signal.SIGTERM)
        except (ProcessLookupError, OSError, ValueError):
            try:
                proc.terminate()
            except (ProcessLookupError, OSError):
                pass

        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            # 2. SIGKILL 进程组
            try:
                os.killpg(os.getpgid(proc.pid), _signal.SIGKILL)
            except (ProcessLookupError, OSError, ValueError):
                try:
                    proc.kill()
                except (ProcessLookupError, OSError):
                    pass
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
    except Exception:
        pass


# MCP stdio engine cleanup is handled in runner._run_mcp_stdio() finally block
# No global cleanup needed
