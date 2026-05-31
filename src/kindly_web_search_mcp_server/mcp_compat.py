from __future__ import annotations

import inspect
import re
from functools import wraps
from typing import Any

from fastmcp import FastMCP


def _sanitize_tool_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_")
    return cleaned or "tool"


def _register_prompt_tool(mcp: FastMCP, prompt: Any) -> None:
    tool_name = f"prompt_{_sanitize_tool_name(prompt.name)}"
    tool_title = prompt.title or prompt.name

    @wraps(prompt.fn)
    async def _tool(**kwargs: Any) -> Any:
        return prompt.render(kwargs or None)

    _tool.__signature__ = inspect.signature(prompt.fn)
    _tool.__name__ = tool_name
    _tool.__doc__ = prompt.description or f"Expose prompt {prompt.name} as a tool."
    mcp.tool(name=tool_name, title=tool_title, description=prompt.description)(_tool)


def _register_resource_tool(mcp: FastMCP, resource: Any) -> None:
    tool_name = f"resource_{_sanitize_tool_name(str(resource.key))}"
    tool_title = resource.title or resource.name

    async def _tool() -> Any:
        return resource.read()

    _tool.__name__ = tool_name
    _tool.__doc__ = resource.description or f"Expose resource {resource.key} as a tool."
    _tool.__signature__ = inspect.Signature()
    mcp.tool(name=tool_name, title=tool_title, description=resource.description)(_tool)


def register_prompt_and_resource_tools(mcp: FastMCP) -> None:
    """Expose registered prompts and resources as tools for tool-only clients."""

    for prompt in mcp._prompt_manager._prompts.values():
        if prompt.enabled:
            _register_prompt_tool(mcp, prompt)

    for resource in mcp._resource_manager._resources.values():
        if resource.enabled:
            _register_resource_tool(mcp, resource)
