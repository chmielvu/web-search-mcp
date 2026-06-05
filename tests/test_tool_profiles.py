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
        {"web_search", "get_content", "batch_get_content", "discover_links"}
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

    assert TOOL_CATALOG["perplexity_search"].expensive is True
    assert TOOL_CATALOG["grok_search"].expensive is True
    assert TOOL_CATALOG["agentic_web_research"].experimental is True
    assert TOOL_CATALOG["analytics_query"].open_world is False
    assert TOOL_CATALOG["analytics_report"].open_world is False
    assert TOOL_CATALOG["quick_web_search"].open_world is True
    assert TOOL_CATALOG["composio_similarlinks"].open_world is True
    assert TOOL_CATALOG["composio_image_search"].open_world is True


def test_profile_membership_matches_visibility_requirements() -> None:
    from kindly_web_search_mcp_server.tools.profiles import tools_for_profile

    assert tools_for_profile("default") == frozenset(
        {"web_search", "get_content", "batch_get_content", "discover_links"}
    )
    assert tools_for_profile("research") == frozenset(
        {
            "web_search",
            "get_content",
            "batch_get_content",
            "discover_links",
            "gemini_search",
            "perplexity_search",
            "academic_search",
            "grok_search",
            "quick_web_search",
            "composio_similarlinks",
            "agentic_web_research",
        }
    )
    assert tools_for_profile("media") == frozenset(
        {
            "web_search",
            "get_content",
            "batch_get_content",
            "discover_links",
            "youtube_search",
            "youtube_transcript",
            "composio_image_search",
        }
    )
    assert tools_for_profile("diagnostic") == frozenset(
        {
            "web_search",
            "get_content",
            "batch_get_content",
            "discover_links",
            "analytics_query",
            "analytics_report",
        }
    )
    assert tools_for_profile("full") == frozenset(
        {
            "web_search",
            "get_content",
            "batch_get_content",
            "discover_links",
            "gemini_search",
            "perplexity_search",
            "academic_search",
            "grok_search",
            "quick_web_search",
            "composio_similarlinks",
            "composio_image_search",
            "analytics_query",
            "analytics_report",
            "agentic_web_research",
            "youtube_search",
            "youtube_transcript",
        }
    )


def test_profile_validation_rejects_unknown_values() -> None:
    from kindly_web_search_mcp_server.tools.profiles import (
        ALLOWED_TOOL_PROFILES,
        normalize_tool_profile,
    )

    assert ALLOWED_TOOL_PROFILES == frozenset(
        {"default", "research", "media", "diagnostic", "experimental", "full"}
    )
    assert normalize_tool_profile(" Research ") == "research"
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
    apply_tool_profile(mcp, "default")

    assert mcp.calls[0] == (
        "enable",
        {
            "tags": {"profile:default"},
            "only": True,
            "components": {"tool"},
        },
    )
    assert mcp.calls[1] == (
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
    def web_search() -> str:
        return "ok"

    @mcp.tool(**tool_kwargs("quick_web_search"))
    def quick_web_search() -> str:
        return "ok"

    @mcp.tool(**tool_kwargs("composio_similarlinks"))
    def composio_similarlinks() -> str:
        return "ok"

    @mcp.tool(**tool_kwargs("analytics_query"))
    def analytics_query() -> str:
        return "ok"

    @mcp.tool(**tool_kwargs("grok_search"))
    def grok_search() -> str:
        return "ok"

    @mcp.tool(**tool_kwargs("agentic_web_research"))
    def agentic_web_research() -> str:
        return "ok"

    apply_tool_profile(mcp, "default")

    assert {tool.name for tool in asyncio.run(mcp.list_tools())} == {"web_search"}
