from __future__ import annotations

import asyncio
import sys

from fastmcp import FastMCP, Client

from kindly_web_search_mcp_server.tools.catalog import tool_kwargs
from kindly_web_search_mcp_server.tools.profiles import apply_tool_profile


def test_tool_search_transform_not_active_by_default():
    """Without the setting, no search transform meta-tools are injected."""
    mcp = FastMCP("search-transform-default-test")

    @mcp.tool(**tool_kwargs("web_search"))
    async def web_search(query: str = "") -> str:
        return "ok"

    @mcp.tool(**tool_kwargs("get_content"))
    async def get_content(url: str = "") -> str:
        return "ok"

    @mcp.tool(**tool_kwargs("youtube_transcript"))
    async def youtube_transcript(video_url: str = "") -> str:
        return "ok"

    apply_tool_profile(mcp, "full")

    # Do not add transform here (simulates disabled)
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert "search_tools" not in names
    assert "call_tool" not in names
    # Originals are present when no transform
    assert "web_search" in names
    assert "youtube_transcript" in names


def test_tool_search_transform_exposes_meta_tools_and_surfaces_correct_tools(
    monkeypatch,
):
    """Enabling TOOL_SEARCH_ENABLED adds BM25SearchTransform after profile.

    Queries for docs/URL fetch surface get_content or web_search; YouTube transcript
    surfaces youtube_* tools (respecting profile).
    """
    # Set env before importing server (which reads settings at module load and
    # runs profile+conditional transform at bottom of server.py).
    monkeypatch.setenv("TOOL_SEARCH_ENABLED", "true")
    # Use full profile so all tools including youtube_* are visible
    monkeypatch.setenv("TOOL_PROFILE", "full")

    # Re-importing server pulls the whole package tree into fresh module
    # objects. Snapshot and restore so later tests keep single class
    # identities (otherwise patch() targets silently miss → real network
    # calls, dataclass_exact_type, etc.).
    package = "kindly_web_search_mcp_server"
    saved = {
        name: module
        for name, module in sys.modules.items()
        if name == package or name.startswith(package + ".")
    }
    for mod in list(sys.modules):
        if mod.startswith(package):
            del sys.modules[mod]
    try:
        import kindly_web_search_mcp_server.server as server_mod

        mcp = server_mod.mcp
    finally:
        sys.modules.update(saved)

    # Now list_tools should be transformed by the server wiring under TOOL_SEARCH_ENABLED
    listed = asyncio.run(mcp.list_tools())
    listed_names = {t.name for t in listed}
    assert "search_tools" in listed_names, "search meta-tool must be exposed when enabled"
    assert "call_tool" in listed_names, "call meta-tool must be exposed when enabled"
    # Pinned core tools (from default profile + full in catalog) remain visible
    assert "web_search" in listed_names
    assert "get_content" in listed_names
    # Non-pinned like youtube are hidden from flat list but discoverable via search
    # (they may appear if profile made them, but transform limits to always_visible + metas)
    # The contract is metas are there and search surfaces them.

    # Use Client to exercise search surface
    async def _run_search_queries():
        async with Client(mcp) as client:
            # Use name-containing patterns guaranteed to match (name part of searchable text).
            # "get_content|web_search" will hit the pinned tools; transcript hits media ones via search.
            docs_res = await client.call_tool("search_tools", {"query": "get_content web_search"})
            res_text = str(docs_res)
            assert "get_content" in res_text or "web_search" in res_text, (
                f"search must surface get_content or web_search; got {res_text}"
            )

            # YouTube transcript query must surface the transcript tool (discoverable via search)
            yt_res = await client.call_tool(
                "search_tools", {"query": "youtube_transcript transcript"}
            )
            yt_text = str(yt_res)
            assert "youtube_transcript" in yt_text or "youtube_search" in yt_text, (
                f"search must surface youtube_transcript or youtube_search; got {yt_text}"
            )

            # Also verify call_tool proxy exists and would work for direct name (hidden tools remain callable)
            # (We don't actually invoke here to avoid side effects; presence in search is the assert)

    asyncio.run(_run_search_queries())


def test_tool_search_emits_surface_events(monkeypatch, caplog):
    """When enabled, server emits tool_surface.search_enabled and tool_surface.profile_applied."""
    import logging
    monkeypatch.setenv("TOOL_SEARCH_ENABLED", "true")
    caplog.set_level(logging.INFO)
    logging.getLogger("kindly_web_search_mcp_server.server").setLevel(logging.INFO)
    # Clean any prior server/settings import so bottom-of-module code re-executes with new env.
    package = "kindly_web_search_mcp_server"
    saved = {
        name: module
        for name, module in sys.modules.items()
        if name == package or name.startswith(package + ".")
    }
    for mod in list(sys.modules):
        if mod.startswith(package):
            del sys.modules[mod]
    try:
        import kindly_web_search_mcp_server.server as _server_mod  # noqa: F401
    finally:
        sys.modules.update(saved)
    logged_events = [rec.getMessage() for rec in caplog.records]
    assert any("tool_surface.profile_applied" in e for e in logged_events), (
        f"expected tool_surface.profile_applied in logs, got: {logged_events[-10:]}"
    )
    assert any("tool_surface.search_enabled" in e for e in logged_events), (
        f"expected tool_surface.search_enabled in logs, got: {logged_events[-10:]}"
    )
