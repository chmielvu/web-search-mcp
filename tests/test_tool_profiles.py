from __future__ import annotations

import asyncio

import pytest
from mcp.types import ToolAnnotations


def test_catalog_declares_stable_public_tool_metadata() -> None:
    from kindly_web_search_mcp_server.tools.catalog import (
        DEFAULT_PROFILE_TOOLS,
        TOOL_CATALOG,
        tool_kwargs,
    )

    assert DEFAULT_PROFILE_TOOLS == frozenset(
        {
            "quick_web_search",
            "code_search",
            "web_search",
            "code_fetch",
            "fetch",
            "gemini_search",
            "generate_sitemap",
            "youtube_search",
            "youtube_transcript",
            "deep_research",
            "recommend_command",
        }
    )

    for name, entry in TOOL_CATALOG.items():
        assert entry.name == name
        assert entry.profiles
        assert entry.tags
        assert "tool:public" in entry.tags
        assert isinstance(entry.annotations, ToolAnnotations)
        assert entry.annotations.title == entry.title
        assert entry.annotations.readOnlyHint == entry.read_only
        assert entry.annotations.idempotentHint == entry.idempotent
        assert entry.annotations.openWorldHint == entry.open_world
        kwargs = tool_kwargs(name)
        assert kwargs["tags"] == entry.tags
        assert kwargs["annotations"] == entry.annotations

    assert TOOL_CATALOG["grok_search"].expensive is True
    assert TOOL_CATALOG["generate_sitemap"].expensive is True
    assert TOOL_CATALOG["youtube_search"].open_world is True
    assert TOOL_CATALOG["youtube_transcript"].open_world is True


def test_profile_membership_matches_visibility_requirements() -> None:
    from kindly_web_search_mcp_server.tools.profiles import tools_for_profile

    assert tools_for_profile("regular") == frozenset(
        {
            "academic_search",
            "code_search",
            "web_search",
            "code_fetch",
            "quick_web_search",
            "fetch",
            "gemini_search",
            "youtube_search",
            "youtube_transcript",
            "generate_sitemap",
            "deep_research",
            "composio_similarlinks",
            "recommend_command",
        }
    )
    assert tools_for_profile("full") == frozenset(
        {
            "academic_search",
            "code_search",
            "code_fetch",
            "fetch",
            "composio_similarlinks",
            "deep_research",
            "gemini_search",
            "generate_sitemap",
            "grok_search",
            "quick_web_search",
            "web_search",
            "youtube_search",
            "youtube_transcript",
            "recommend_command",
        }
    )


def test_profile_validation_rejects_unknown_values() -> None:
    from kindly_web_search_mcp_server.tools.profiles import (
        ALLOWED_TOOL_PROFILES,
        normalize_tool_profile,
    )

    assert ALLOWED_TOOL_PROFILES == frozenset({"regular", "full"})
    assert normalize_tool_profile(" Full ") == "full"
    with pytest.raises(ValueError, match="tool_profile"):
        normalize_tool_profile("unknown")


def test_apply_tool_profile_uses_fastmcp_tag_visibility() -> None:
    from kindly_web_search_mcp_server.tools.profiles import apply_tool_profile

    class DummyMCP:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def enable(self, **kwargs):
            self.calls.append(("enable", kwargs))
            return self

        def disable(self, **kwargs):
            self.calls.append(("disable", kwargs))
            return self

    mcp = DummyMCP()
    apply_tool_profile(mcp, "regular")

    assert mcp.calls[0] == (
        "enable",
        {
            "tags": {"profile:regular"},
            "only": True,
            "components": {"tool"},
        },
    )
    assert mcp.calls[1] == (
        "enable",
        {
            "components": {"resource", "template", "prompt"},
        },
    )
    assert mcp.calls[2] == (
        "disable",
        {
            "tags": {"tool:experimental"},
            "components": {"tool"},
        },
    )


def test_apply_tool_profile_filters_real_fastmcp_tools_by_tag() -> None:
    from fastmcp import FastMCP

    from kindly_web_search_mcp_server.tools.catalog import tool_kwargs
    from kindly_web_search_mcp_server.tools.profiles import apply_tool_profile

    mcp = FastMCP("profile-test")

    @mcp.tool(**tool_kwargs("web_search"))
    async def web_search() -> str:
        return "ok"

    @mcp.tool(**tool_kwargs("quick_web_search"))
    async def quick_web_search() -> str:
        return "ok"

    @mcp.tool(**tool_kwargs("composio_similarlinks"))
    async def composio_similarlinks() -> str:
        return "ok"

    @mcp.tool(**tool_kwargs("academic_search"))
    async def academic_search() -> str:
        return "ok"

    @mcp.tool(**tool_kwargs("grok_search"))
    async def grok_search() -> str:
        return "ok"

    apply_tool_profile(mcp, "regular")

    tool_names = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert {"web_search", "quick_web_search"}.issubset(tool_names)
    assert {"academic_search", "composio_similarlinks"}.issubset(tool_names)
    assert "grok_search" not in tool_names
