"""Argument Aliasing Middleware.

Intercepts tool calls before validation and rewrites hallucinated parameter names
to their canonical definitions. Uses FastMCP 4.x immutable context.copy pattern.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

logger = logging.getLogger(__name__)

GLOBAL_ALIASES: dict[str, list[str]] = {
    "query": ["userQuery", "question", "search_term", "searchQuery", "q"],
    "url": ["link", "target_url", "pageUrl", "page_url", "target", "uri"],
    "urls": ["url_list", "links", "pages"],
    "video_id_or_url": ["videoId", "video_url", "youtube_url", "video"],
}

TOOL_ALIASES: dict[str, dict[str, list[str]]] = {
    "youtube_transcript": {
        "output_format": ["format", "response_format"],
    },
}


class ArgumentAliasingMiddleware(Middleware):
    """Rewrites common LLM parameter hallucinations to canonical parameter names using FastMCP 4.x patterns."""

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Any],
    ) -> Any:
        tool_name = context.message.name
        args = context.message.arguments

        if isinstance(args, dict):
            new_args = dict(args)
            modified = False

            # 1. Apply tool-specific aliases, then the global set.
            modified |= _apply_aliases(
                new_args,
                TOOL_ALIASES.get(tool_name, {}),
                scope="tool-specific",
                tool_name=tool_name,
            )
            modified |= _apply_aliases(
                new_args, GLOBAL_ALIASES, scope="global", tool_name=tool_name
            )

            if modified:
                new_message = context.message.model_copy(update={"arguments": new_args})
                context = context.copy(message=new_message)

        return await call_next(context)


def _apply_aliases(
    args: dict[str, Any],
    aliases: dict[str, list[str]],
    *,
    scope: str,
    tool_name: str,
) -> bool:
    """Rewrite alias keys to their canonical name; return whether anything changed.

    The canonical value always wins: when a caller sends both names, the alias is
    dropped rather than forwarded, because an unknown keyword argument makes the
    tool call fail validation.
    """
    modified = False
    for canonical, alternatives in aliases.items():
        for alias in alternatives:
            if alias not in args:
                continue
            value = args.pop(alias)
            modified = True
            if canonical in args:
                logger.info(
                    "Dropped redundant %s alias '%s' for tool '%s' (canonical '%s' already set)",
                    scope,
                    alias,
                    tool_name,
                    canonical,
                )
                continue
            args[canonical] = value
            logger.info(
                "Rewrote %s alias '%s' -> '%s' for tool '%s'",
                scope,
                alias,
                canonical,
                tool_name,
            )
    return modified


def create_argument_aliasing_middleware() -> ArgumentAliasingMiddleware:
    return ArgumentAliasingMiddleware()


__all__ = [
    "GLOBAL_ALIASES",
    "TOOL_ALIASES",
    "ArgumentAliasingMiddleware",
    "create_argument_aliasing_middleware",
]
