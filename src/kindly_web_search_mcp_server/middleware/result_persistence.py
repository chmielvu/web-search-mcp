from __future__ import annotations

from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult

from ..cli.services.results import persist_mcp_result


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _result_payload(result: Any) -> Any:
    if isinstance(result, ToolResult):
        structured = result.structured_content
        if isinstance(structured, dict):
            payload = _jsonable(structured)
            if getattr(result, "is_error", False) and isinstance(payload, dict):
                payload.setdefault("_mcp_is_error", True)
            return payload
        return {
            "content": _jsonable(result.content),
            "is_error": bool(getattr(result, "is_error", False)),
        }
    return _jsonable(result)


class ResultPersistenceMiddleware(Middleware):
    """Persist completed MCP tool responses without changing their wire result."""

    async def on_call_tool(self, context: MiddlewareContext, call_next: Any) -> Any:
        tool_name = context.message.name
        result = await call_next(context)
        persist_mcp_result(tool_name, _result_payload(result))
        return result


def create_result_persistence_middleware() -> ResultPersistenceMiddleware:
    return ResultPersistenceMiddleware()


__all__ = ["ResultPersistenceMiddleware", "create_result_persistence_middleware"]
