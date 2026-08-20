"""Guard against stray stdout writes corrupting the stdio JSON-RPC stream.

MCP's stdio transport (``mcp.server.stdio.stdio_server``) captures
``sys.stdout.buffer`` exactly once, at transport startup, and treats the
rest of the process's lifetime as a newline-delimited JSON-RPC channel: one
``model_dump_json(...)`` line per response. Any code anywhere in the
tool-call path that writes to real stdout -- a bare ``print()``, a library
that defaults its console output to stdout instead of stderr -- injects a
non-JSON line into that exact same byte stream. The client's line-based
parser then fails to parse the corrupted line (or the real response gets
interleaved with it), so it never receives a usable response and hangs
until its own client-side timeout fires, even though the server actually
finished the work.

Confirmed offender: ``rank_llm`` (the LLM reranker used by the web_search
rerank stage) prints status messages -- "Template validated successfully!",
"Loading {model} ...", "Completed loading {model}" -- directly via
``print()`` throughout its vendored source (inference_handler.py,
rankllm.py, reranker.py, listwise_rankllm.py, rank_gpt.py, rank_gemini.py).
This is exactly why ``web_search`` (the only tool that reaches rank_llm)
hangs/"Transport closed"s over the MCP stdio transport while completing
normally as a bare coroutine call (no JSON-RPC framing involved), and why
other tools on the same transport (get_content, tools/list) are unaffected.

The redirect is deliberately scoped to ``on_call_tool``. Do not move it to
``on_request``: FastMCP's stdio response writer runs inside the request
middleware chain and resolves ``sys.stdout`` dynamically. A request-wide
redirect would send valid JSON-RPC responses to stderr and make clients report
``Transport closed``.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext


class StdoutGuardMiddleware(Middleware):
    """Redirect stdout only while a tool implementation is running."""

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Awaitable[Any]],
    ) -> object:
        with contextlib.redirect_stdout(sys.stderr):
            return await call_next(context)


def create_stdout_guard_middleware() -> StdoutGuardMiddleware:
    """Factory matching the pattern used by other middleware modules."""
    return StdoutGuardMiddleware()
