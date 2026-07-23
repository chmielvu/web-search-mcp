"""Argument Aliasing Middleware.

Intercepts tool calls before validation and rewrites hallucinated parameter names
to their canonical definitions. Uses FastMCP 4.x immutable context.copy pattern.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

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

            # 1. Apply tool-specific aliases
            tool_specific = TOOL_ALIASES.get(tool_name, {})
            for canonical, alternatives in tool_specific.items():
                if canonical not in new_args:
                    for alt in alternatives:
                        if alt in new_args:
                            new_args[canonical] = new_args.pop(alt)
                            modified = True
                            logger.info(
                                "Rewrote tool-specific alias '%s' -> '%s' for tool '%s'",
                                alt,
                                canonical,
                                tool_name,
                            )
                            break

            # 2. Apply global aliases
            for canonical, alternatives in GLOBAL_ALIASES.items():
                if canonical not in new_args:
                    for alt in alternatives:
                        if alt in new_args:
                            new_args[canonical] = new_args.pop(alt)
                            modified = True
                            logger.info(
                                "Rewrote global alias '%s' -> '%s' for tool '%s'",
                                alt,
                                canonical,
                                tool_name,
                            )
                            break

            if modified:
                new_message = context.message.model_copy(update={"arguments": new_args})
                context = context.copy(message=new_message)

        return await call_next(context)


def create_argument_aliasing_middleware() -> ArgumentAliasingMiddleware:
    return ArgumentAliasingMiddleware()


__all__ = [
    "ArgumentAliasingMiddleware",
    "create_argument_aliasing_middleware",
    "GLOBAL_ALIASES",
    "TOOL_ALIASES",
]
