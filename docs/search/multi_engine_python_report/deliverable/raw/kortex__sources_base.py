"""Source base class + subprocess helpers (retry + opencli serialization)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from abc import ABC, abstractmethod

from ..config import (
    BLOCK_DETECTION,
    PER_SOURCE_TIMEOUT,
    RETRY_BACKOFF,
    RETRY_COUNT,
    RETRYABLE_EXIT_CODES,
)
from ..extract.detectors import classify
from ..extract.scheduler import browser_lease
from ..models import Result

logger = logging.getLogger("kortex_search.sources")

# opencli sources share a single browser bridge (one tab lease at a time), so
# their commands are serialized — now through the extraction layer's browser
# budget (KORTEX_SEARCH_BROWSER_BUDGET, default 2) instead of a hardcoded
# single-slot semaphore. `browser_lease` is re-exported below for callers.

# Subprocess env allowlist: CLI backends get the runtime essentials only.
# Secrets (DEEPSEEK_API_KEY, GITHUB_TOKEN, …) must NOT reach subprocesses —
# unpinned helpers like `uvx mcp-server-linkedin@latest` would otherwise see
# them. Source-specific auth (twitter) is passed explicitly via `env=`.
_ENV_ALLOWLIST = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
    "TERM", "TMPDIR", "NO_COLOR", "CLICOLOR", "FORCE_COLOR",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
    "UV_CACHE_DIR", "UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_PYTHON_INSTALL_DIR",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy",
    "https_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "OPENCLI_CONFIG",
}


def _subprocess_env(extra: dict | None = None) -> dict:
    """Minimal env for subprocesses: allowlist + explicit extras."""
    env = {k: v for k, v in os.environ.items() if k in _ENV_ALLOWLIST}
    if extra:
        env.update(extra)
    return env


def _sanitize_text(text: str, cap: int = 200) -> str:
    """Strip control characters + cap length for tool-result error text
    (prevents terminal-control injection via subprocess stderr)."""
    cleaned = "".join(ch for ch in (text or "") if ch >= " " or ch in "\n\t")
    return cleaned[:cap]


def guard_query(query: str) -> str:
    """Reject CLI-unsafe queries: a leading '-' would be parsed as a flag by
    twitter-cli/opencli/yt-dlp (option injection). Fail loudly instead."""
    q = query.strip()
    if q.startswith("-"):
        raise SourceError(
            f"query must not start with '-' (CLI flag-injection guard): {q[:80]!r}"
        )
    return q


def normalize_published(value) -> str | None:
    """Normalize a source's date field into a `_parse_date`-friendly string.

    Adapters emit wildly different shapes: ISO strings, epoch seconds
    (v2ex/stackoverflow), bare years (semantic_scholar/crossref). The gateway's
    freshness filter can only read ISO-ish strings, so unnormalized values
    silently defeated it. Rules:
      - falsy → None
      - "YYYY" → "YYYY-01-01"
      - 8 digits → compact date "YYYYMMDD" (already parseable; passthrough)
      - 9-11 digits → epoch seconds (or ms) → ISO with Z
      - anything else → passthrough (best effort; kept by the freshness filter)
    """
    import datetime as _dt
    import re as _re

    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    if _re.fullmatch(r"\d{4}", s):  # bare year
        return f"{s}-01-01"
    if _re.fullmatch(r"\d{8}", s):  # compact date — already parseable
        return s
    if _re.fullmatch(r"\d{9,11}", s):  # epoch seconds (ms unlikely at 11)
        try:
            ts = int(s)
            if ts > 10_000_000_000:  # ms precision — trim
                ts //= 1000
            return _dt.datetime.fromtimestamp(
                ts, tz=_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, OSError, OverflowError):
            return s
    return s


class SourceError(Exception):
    """Raised when a source cannot fulfil a query."""


def _blocked_error(code: int, text: str, source: str | None = None) -> SourceError | None:
    """Classify a response for challenge markers; None when clean.

    A challenge is the one failure retry logic must NOT touch — retrying a
    wall is the wrong move, so a detected block raises immediately (before
    the retryable-exit-code check) and the orchestrator's ladder decides.
    Block events are recorded for telemetry here (the raise site); the
    envelope layer only records non-raising blocked strings — never the same
    event twice (see orchestrator._extract_signals).
    """
    if not BLOCK_DETECTION:
        return None
    sig = classify(code, None, text)
    if sig is None:
        return None
    from ..stats import record_block
    record_block(source or "cli", sig.vendor, sig.level)
    return SourceError(f"blocked ({sig.vendor}/{sig.level}): {sig.evidence}")


async def run_cmd(
    cmd: list[str],
    timeout: int = PER_SOURCE_TIMEOUT,
    env: dict | None = None,
    retries: int = RETRY_COUNT,
    source: str | None = None,
) -> tuple[int, str]:
    """Run a command, returning (returncode, combined_stdout).

    Retries transient failures (timeouts and `RETRYABLE_EXIT_CODES`) with
    exponential backoff. Non-transient exit codes (auth failures, 404s, …)
    fail immediately — retrying them wastes the fan-out budget. `command not
    found` is not retried. Detected challenge walls also fail immediately
    (a `blocked (vendor/level)` SourceError) — retries never touch them.
    `source` names the gateway source for block telemetry.
    """
    full_env = _subprocess_env(env)
    last_err: SourceError | None = None
    for attempt in range(retries + 1):
        if attempt > 0:
            await asyncio.sleep(RETRY_BACKOFF * (2 ** (attempt - 1)))
        try:
            # stdin=DEVNULL is CRITICAL: children must never inherit the
            # server's fd 0 — that is the MCP stdio protocol pipe. A child
            # chain that reads its own stdin (mcporter → npx → the exa MCP
            # server) becomes a second reader on the client's protocol
            # stream and STEALS client messages. The session's receive loop
            # then starves and unwinds, the process exits cleanly (rc=0)
            # mid-request, and every in-flight call dies with "Connection
            # closed" (root-caused 2026-09-03 via pipe-inode forensics).
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=full_env,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            code = proc.returncode or 0
            text = out.decode("utf-8", "replace")
            if code == 0:
                return code, text
            blocked = _blocked_error(code, text, source)
            if blocked is not None:
                raise blocked
            detail = _sanitize_text(text, 200)
            if RETRYABLE_EXIT_CODES and code not in RETRYABLE_EXIT_CODES:
                # non-transient (auth/404/usage): fail now, don't burn retries
                raise SourceError(f"exit {code}: {' '.join(cmd)}: {detail}")
            last_err = SourceError(f"exit {code}: {' '.join(cmd)}: {detail}")
        except TimeoutError:
            with contextlib.suppress(Exception):  # best-effort kill
                proc.kill()
            last_err = SourceError(f"timeout ({timeout}s): {' '.join(cmd)}")
        except asyncio.CancelledError:
            # the outer task was cancelled (GLOBAL_TIMEOUT fired, client
            # disconnected) — kill the child or it ORPHANS and keeps running
            # (bug-sweep discovery 2026-08-26)
            with contextlib.suppress(Exception):
                proc.kill()
            raise
        except FileNotFoundError:
            raise SourceError(f"command not found: {cmd[0]}") from None
    raise last_err  # type: ignore[misc]


def _extract_json(text: str):
    """Parse the outermost JSON value, tolerating CLI banner noise.

    Subprocess backends print banners around their payloads — systemd-run's
    "Running as unit: …" (now suppressed with --quiet, but older wrappers /
    update checks vary) and opencli's "Update available: …" trailer. A banner
    at either end would otherwise silently degrade the whole parse to None
    (smoke-test discovery 2026-08-25). Strategy: walk the text, find the
    outermost JSON object/array span, and parse just that slice.
    """
    start = None
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch in "[{":
            if start is None:
                start = i
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_json_or_yaml(text: str):
    """Parse JSON first, then YAML (some CLIs emit one or the other)."""
    text = text.strip()
    if not text:
        return None
    span = _extract_json(text)
    if span is not None:
        try:
            return json.loads(span)
        except json.JSONDecodeError:
            pass
    import yaml  # local import: optional
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


async def run_opencli(cmd: list[str], timeout: int = PER_SOURCE_TIMEOUT,
                      env: dict | None = None,
                      retries: int = RETRY_COUNT,
                      source: str | None = None) -> tuple[int, str]:
    """Run an opencli command under the browser budget (default 2 concurrent
    browser ops) instead of the old single-slot lock.

    L3 enforcement (D7.1): browser-tier ops REFUSE to launch without the
    kernel egress filter installed (EgressUnhardened → the explicit
    `blocked (egress-unhardened)` SourceError the envelope names). When the
    filter is live but the gateway itself is not inside the scoped cgroup,
    the child is wrapped in a `systemd-run --user --scope` transient scope
    (pam_authnft pattern) so the kernel rules see its sockets; the fixed
    unit name implies a serialized browser budget for ad-hoc scoped mode.
    """
    from ..extract import harden
    try:
        harden.enforce()
    except harden.EgressUnhardened as exc:
        raise SourceError(str(exc)) from None
    async with browser_lease(cmd[0] if cmd else "opencli"):
        st = harden.status()
        if st["installed"] and not st["covered"]:
            scoped = ["systemd-run", "--user", "--scope", "--quiet",
                      "--collect", "--unit", "ks-egress", *cmd]
            return await run_cmd(scoped, timeout=timeout, env=env,
                                 retries=retries, source=source)
        return await run_cmd(cmd, timeout=timeout, env=env, retries=retries,
                             source=source)


async def run_profile(profile, argv: list[str],
                      timeout: float = PER_SOURCE_TIMEOUT,
                      source: str | None = None) -> tuple[int, str]:
    """Run an agent-browser command against a managed profile farm browser.

    The profile's browser is launched on first touch (egress-gated, see
    `extract/browserfarm.py`) and its login state persists across calls.
    Pacing is per-profile and jittered; the browser budget is the same
    lease the OpenCLI tier uses. This is the farm tier of the fallback
    ladder — callers try it before `run_opencli`.
    """
    from ..config import RATE_LIMIT_INTERVAL
    from ..extract import browserfarm
    from ..extract.scheduler import paced

    await paced(profile.name, RATE_LIMIT_INTERVAL)
    async with browser_lease(profile.name):
        ok, detail = await browserfarm.ensure(profile)
        if not ok:
            raise SourceError(f"profile {profile.name} unavailable: {detail}")
        code, out = await browserfarm.exec_sync(profile, argv, timeout=timeout)
        blocked = _blocked_error(code, out, source)
        if blocked is not None:
            raise blocked
        return code, out


class Source(ABC):
    """A pluggable search backend."""

    name: str = ""
    description: str = ""
    source_type: str = "web"  # paper|post|video|repo|web|forum|news|doc|code

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[Result]:
        """Return ranked results for query (best first)."""

    async def available(self) -> tuple[bool, str]:
        """Quick health probe (command/endpoint present)."""
        return True, ""

    def _result(self, title: str, url: str, snippet: str = "", engine: str = "",
                **meta) -> Result:
        meta.setdefault("source_type", self.source_type)
        return Result(title=title or "", url=url or "", snippet=snippet or "",
                      source=self.name, engine=engine, meta=meta)
