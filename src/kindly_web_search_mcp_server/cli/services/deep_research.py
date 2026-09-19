from __future__ import annotations

import asyncio
from typing import Any, cast

from fastmcp.dependencies import Progress
from fastmcp.server.context import Context

from ...tools.deep_research import deep_research


class _CliContext:
    async def info(self, *_: Any, **__: Any) -> None:
        return None

    async def warning(self, *_: Any, **__: Any) -> None:
        return None


class _CliProgress:
    async def set_total(self, _: int) -> None:
        return None

    async def set_message(self, _: str) -> None:
        return None

    async def increment(self) -> None:
        return None


class DeepResearchTimeoutError(TimeoutError):
    """Raised when the deep-research backend exceeds the CLI-imposed timeout."""

    def __init__(self, timeout: float) -> None:
        super().__init__(f"Deep research timed out after {timeout} seconds.")
        self.timeout = timeout


async def fetch_deep_research_payload(
    query: str,
    *,
    depth: str = "standard",
    with_images: bool = False,
    language_code: str | None = None,
    token_budget_override: int | None = None,
    team_size_override: int | None = None,
    endpoint_override: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    coro = deep_research(
        query=query,
        depth=depth,
        with_images=with_images,
        language_code=language_code,
        token_budget_override=token_budget_override,
        team_size_override=team_size_override,
        endpoint_override=endpoint_override,
        ctx=cast(Context, _CliContext()),
        progress=cast(Progress, _CliProgress()),
    )
    if timeout is None:
        response = await coro
    else:
        try:
            response = await asyncio.wait_for(coro, timeout=timeout)
        except TimeoutError as exc:
            raise DeepResearchTimeoutError(timeout) from exc
    return response.model_dump(exclude_none=True)
