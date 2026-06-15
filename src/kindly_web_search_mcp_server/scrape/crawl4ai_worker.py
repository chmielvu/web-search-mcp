"""Subprocess entry point for Crawl4AI-based HTML fetching.

Replaces nodriver_worker.py with the same subprocess protocol:
  - stdout → HTML bytes only (MCP-protocol-safe)
  - stderr → DIAG {json} lines for diagnostics
  - _NullTextIO suppresses all third-party output

Crawl4AI uses Playwright internally, which manages its own browser lifecycle.
No need for ChromiumPool, port picking, or DevTools probing.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import platform
import sys
import time
from typing import TextIO


# ---------------------------------------------------------------------------
# Subprocess utilities (shared protocol with nodriver_worker)
# ---------------------------------------------------------------------------


class _NullTextIO(io.TextIOBase):
    """A text sink that discards writes but preserves file-descriptor APIs."""

    def __init__(self, wrapped: TextIO) -> None:
        self._wrapped = wrapped

    def write(self, s: str) -> int:  # type: ignore[override]
        return len(s)

    def flush(self) -> None:  # type: ignore[override]
        return None

    def fileno(self) -> int:  # type: ignore[override]
        return self._wrapped.fileno()

    def isatty(self) -> bool:  # type: ignore[override]
        try:
            return self._wrapped.isatty()
        except Exception:
            return False

    @property
    def buffer(self):  # type: ignore[override]
        return getattr(self._wrapped, "buffer", None)


def _safe_write_text(stream: TextIO, text: str) -> None:
    """Best-effort write to a text stream without raising encoding errors."""
    msg = (text or "").rstrip() + "\n"
    try:
        buf = getattr(stream, "buffer", None)
        if buf is not None:
            buf.write(msg.encode("utf-8", errors="backslashreplace"))
            buf.flush()
            return
    except Exception:
        pass
    try:
        stream.write(msg)
        stream.flush()
    except Exception:
        try:
            os.write(2, msg.encode("utf-8", errors="backslashreplace"))
        except Exception:
            return


def _safe_write_bytes(stream: TextIO, data: bytes) -> None:
    """Best-effort write raw bytes to a stream."""
    payload = data or b""
    try:
        buf = getattr(stream, "buffer", None)
        if buf is not None:
            buf.write(payload)
            buf.flush()
            return
    except Exception:
        pass
    try:
        os.write(1, payload)
    except Exception:
        return


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

_DIAG_ENABLED = False
_DIAG_REQUEST_ID = "unknown"
_DIAG_STREAM: TextIO | None = None
_DIAG_STARTED = 0.0
_DIAG_LINE_LIMIT = 8000


def _diagnostics_enabled() -> bool:
    raw = (os.environ.get("DIAGNOSTICS") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _emit_diag(stage: str, msg: str, data: dict[str, object] | None = None) -> None:
    if not _DIAG_ENABLED:
        return
    try:
        stream = _DIAG_STREAM or sys.stderr
        elapsed_ms = int((time.monotonic() - _DIAG_STARTED) * 1000)
        entry = {
            "request_id": _DIAG_REQUEST_ID,
            "stage": stage,
            "msg": msg,
            "elapsed_ms": elapsed_ms,
            "data": data or {},
        }
        payload = json.dumps(entry, ensure_ascii=True, separators=(",", ":"))
        if len(payload) > _DIAG_LINE_LIMIT:
            entry = {
                "request_id": _DIAG_REQUEST_ID,
                "stage": stage,
                "msg": msg,
                "elapsed_ms": elapsed_ms,
                "line_truncated": True,
                "data": {
                    "note": "diagnostic payload truncated",
                    "original_len": len(payload),
                },
            }
            payload = json.dumps(entry, ensure_ascii=True, separators=(",", ":"))
        _safe_write_text(stream, f"DIAG {payload}")
    except Exception:
        return


# ---------------------------------------------------------------------------
# Shutdown noise suppression
# ---------------------------------------------------------------------------


def _suppress_unraisable_exceptions() -> None:
    """Prevent shutdown-time unraisable exception noise from leaking to stderr."""
    original = getattr(sys, "unraisablehook", None)
    if not callable(original):
        return

    def filtered(unraisable):  # type: ignore[no-untyped-def]
        exc = getattr(unraisable, "exc_value", None)
        msg = str(exc) if exc is not None else ""
        err_msg = str(getattr(unraisable, "err_msg", "") or "")

        if isinstance(exc, ValueError) and "I/O operation on closed pipe" in msg:
            return
        if (
            "BaseSubprocessTransport.__del__" in err_msg
            or "ProactorBasePipeTransport.__del__" in err_msg
        ):
            return
        return original(unraisable)

    sys.unraisablehook = filtered  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Crawl4AI fetch
# ---------------------------------------------------------------------------


def _resolve_worker_timeout_seconds() -> float:
    """Resolve effective worker timeout from env, clamped to [1, 600]."""
    raw = (os.environ.get("HTML_TOTAL_TIMEOUT_SECONDS") or "").strip()
    try:
        value = float(raw) if raw else 60.0
    except ValueError:
        value = 60.0
    if value <= 0:
        value = 60.0
    return max(1.0, min(value, 600.0))


async def _fetch_html(
    url: str,
    *,
    referer: str | None,
    user_agent: str,
    wait_seconds: float,
    overall_timeout_seconds: float,
) -> str:
    """Fetch rendered HTML via Crawl4AI (Playwright-backed)."""
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
    except ImportError as exc:
        raise RuntimeError(
            "crawl4ai is required for browser-based HTML loading. "
            "Install with: pip install crawl4ai && crawl4ai-setup"
        ) from exc

    started = time.monotonic()

    headers: dict[str, str] = {}
    if referer:
        headers["Referer"] = referer

    browser_config = BrowserConfig(
        headless=True,
        user_agent=user_agent,
    )

    # Convert wait_seconds to milliseconds for Playwright's wait_until + delay
    page_timeout_ms = int(overall_timeout_seconds * 1000)
    run_config = CrawlerRunConfig(
        wait_until="domcontentloaded",
        delay_before_return_html=wait_seconds,
        page_timeout=page_timeout_ms,
        verbose=False,
    )

    _emit_diag(
        "worker.config",
        "Crawl4AI configuration",
        {
            "url": url,
            "referer": referer or "",
            "user_agent": user_agent,
            "wait_seconds": wait_seconds,
            "overall_timeout_seconds": overall_timeout_seconds,
            "page_timeout_ms": page_timeout_ms,
            "pid": os.getpid(),
        },
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(
            url=url,
            config=run_config,
        )

    if not result.success:
        error_msg = result.error_message or "crawl4ai returned success=False"
        raise RuntimeError(f"Crawl4AI fetch failed: {error_msg}")

    html = result.html or ""
    _emit_diag(
        "worker.fetch_complete",
        "Crawl4AI fetch complete",
        {
            "html_len": len(html),
            "status_code": result.status_code,
            "elapsed_seconds": time.monotonic() - started,
        },
    )
    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def _main_async(args: argparse.Namespace) -> int:
    _suppress_unraisable_exceptions()

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    global _DIAG_ENABLED, _DIAG_REQUEST_ID, _DIAG_STREAM, _DIAG_STARTED
    _DIAG_ENABLED = _diagnostics_enabled()
    _DIAG_REQUEST_ID = (os.environ.get("REQUEST_ID") or "unknown").strip() or "unknown"
    _DIAG_STREAM = original_stderr
    _DIAG_STARTED = time.monotonic()

    if _DIAG_ENABLED:
        _emit_diag(
            "worker.start",
            "Crawl4AI worker starting",
            {
                "url": args.url,
                "referer": args.referer or "",
                "user_agent": args.user_agent,
                "wait_seconds": args.wait_seconds,
                "pid": os.getpid(),
                "ppid": os.getppid(),
                "cwd": os.getcwd(),
                "executable": sys.executable,
                "python_version": platform.python_version(),
                "platform": platform.platform(),
            },
        )

    # Suppress stdout/stderr to protect MCP stdio
    sys.stdout = _NullTextIO(original_stdout)
    sys.stderr = _NullTextIO(original_stderr)

    worker_timeout = _resolve_worker_timeout_seconds()

    _emit_diag(
        "worker.timeout_budget",
        "Resolved worker timeout budget",
        {"effective_timeout_seconds": worker_timeout},
    )

    try:
        html = await _fetch_html(
            args.url,
            referer=args.referer,
            user_agent=args.user_agent,
            wait_seconds=args.wait_seconds,
            overall_timeout_seconds=worker_timeout,
        )
        _emit_diag(
            "worker.done",
            "Worker completed",
            {"html_len": len(html or "")},
        )
    except Exception as exc:
        _emit_diag(
            "worker.error",
            "Worker failed",
            {"error": type(exc).__name__, "detail": str(exc)},
        )
        _safe_write_text(original_stderr, f"{type(exc).__name__}: {exc}")
        return 1

    _safe_write_bytes(original_stdout, (html or "").encode("utf-8", errors="strict"))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch rendered HTML via Crawl4AI (Playwright-backed)."
    )
    parser.add_argument("--url", required=True)
    parser.add_argument("--referer", required=False, default=None)
    parser.add_argument("--user-agent", required=True)
    parser.add_argument("--wait-seconds", type=float, default=2.0)
    args = parser.parse_args()

    os.environ.setdefault("PYTHONUNBUFFERED", "1")

    original_stderr = sys.stderr
    try:
        return asyncio.run(_main_async(args))
    except KeyboardInterrupt:  # pragma: no cover
        return 130
    except Exception as exc:  # pragma: no cover
        _safe_write_text(original_stderr, f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
