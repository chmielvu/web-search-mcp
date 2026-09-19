"""CLI adapter for the Crawl4AI-backed ``crawl_web`` tool."""

from __future__ import annotations

from typing import Any, Literal, cast

from fastmcp.dependencies import Progress
from fastmcp.server.context import Context

from ...models import CrawlWebRequest
from ...tools.content import crawl_web

# Mirrors ``CrawlWebRequest.response_format``; the CLI option stays a Literal.
ResponseFormat = Literal["summary", "detailed"]


class _CliContext:
    """Minimal stand-in satisfying the ``Context`` surface ``crawl_web`` touches."""

    is_background_task: bool = False

    async def info(self, *_: Any, **__: Any) -> None:
        return None

    async def warning(self, *_: Any, **__: Any) -> None:
        return None


class _CliProgress:
    """No-op progress sink used when the CLI invokes the tool outside fastmcp."""

    async def set_total(self, _: int) -> None:
        return None

    async def set_message(self, _: str) -> None:
        return None

    async def increment(self) -> None:
        return None


async def fetch_crawl_payload(
    *,
    urls: list[str],
    max_depth: int = 0,
    max_pages: int = 20,
    include_external: bool = False,
    response_format: ResponseFormat = "summary",
) -> dict[str, Any]:
    """Call ``crawl_web`` with a CLI-shaped request envelope.

    The tool's ``targets``/``interaction`` boundaries are nested model objects
    that are not trivially mappable to flat CLI flags, so they stay out of this
    adapter's signature until callers need to express them via JSON passthrough.
    """
    request = CrawlWebRequest(
        urls=urls,
        max_depth=max_depth,
        max_pages=max_pages,
        include_external=include_external,
        response_format=response_format,
    )
    response = await crawl_web(
        request=request,
        ctx=cast(Context, _CliContext()),
        progress=cast(Progress, _CliProgress()),
    )
    return response.model_dump(exclude_none=True)
