from __future__ import annotations

from typing import Literal, Protocol, cast

from .catalog import TOOL_CATALOG

ToolProfile = Literal[
    "regular",
    "full",
]

ALLOWED_TOOL_PROFILES = frozenset(ToolProfile.__args__)  # type: ignore[attr-defined]

# Tools registered but hidden from MCP clients (tools/list) via an explicit
# disable() after profile selection (FastMCP: last Visibility transform wins).
# Implementations stay importable — CLI services call the functions directly
# and bypass MCP visibility — so this only removes the client-facing surface.
DISABLED_TOOLS = frozenset({"code_fetch", "composio_similarlinks", "youtube_transcript"})


class VisibilityServer(Protocol):
    """The slice of the MCP server that profile visibility needs.

    Declared with FastMCP's actual keyword parameters: a protocol that accepts
    arbitrary ``**kwargs`` is not satisfied by a method taking named ones, which is
    what made the real server look incompatible.
    """

    def enable(
        self,
        *,
        tags: set[str] | None = None,
        components: set[Literal["tool", "resource", "template", "prompt"]] | None = None,
        only: bool = False,
    ) -> object: ...

    def disable(
        self,
        *,
        tags: set[str] | None = None,
        components: set[Literal["tool", "resource", "template", "prompt"]] | None = None,
    ) -> object: ...


def normalize_tool_profile(raw: str) -> ToolProfile:
    profile = raw.strip().lower()
    if profile not in ALLOWED_TOOL_PROFILES:
        allowed = ", ".join(sorted(ALLOWED_TOOL_PROFILES))
        raise ValueError(f"tool_profile must be one of: {allowed}. Got {raw!r}.")
    return cast(ToolProfile, profile)


def tools_for_profile(profile: str) -> frozenset[str]:
    normalized = normalize_tool_profile(profile)
    return frozenset(name for name, entry in TOOL_CATALOG.items() if normalized in entry.profiles)


def tags_for_profile(profile: str) -> set[str]:
    normalized = normalize_tool_profile(profile)
    return {f"profile:{normalized}"}


def apply_tool_profile(mcp: VisibilityServer, profile: str) -> VisibilityServer:
    normalized = normalize_tool_profile(profile)
    mcp.enable(tags=tags_for_profile(normalized), only=True, components={"tool"})
    # enable(only=True) disables everything via Visibility(False, match_all=True),
    # then re-enables only matching tools. Resources and prompts must be explicitly
    # re-enabled since they carry no profile tags.
    mcp.enable(components={"resource", "template", "prompt"})
    if normalized == "regular":
        mcp.disable(tags={"tool:experimental"}, components={"tool"})
    # Hide retired tools from every profile. Runs after the profile allowlist so
    # it wins over the enable() above; the opt-in BM25SearchTransform respects
    # prior visibility gates, so hidden tools stay out of search as well.
    mcp.disable(tags={f"tool:{name}" for name in DISABLED_TOOLS}, components={"tool"})
    return mcp
