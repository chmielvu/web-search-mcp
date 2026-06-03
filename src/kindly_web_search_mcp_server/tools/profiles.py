from __future__ import annotations

from typing import Literal, Protocol, cast

from .catalog import TOOL_CATALOG

ToolProfile = Literal[
    "default",
    "research",
    "media",
    "diagnostic",
    "experimental",
    "full",
]

ALLOWED_TOOL_PROFILES = frozenset(ToolProfile.__args__)  # type: ignore[attr-defined]


class VisibilityServer(Protocol):
    def enable(self, **kwargs: object) -> "VisibilityServer": ...

    def disable(self, **kwargs: object) -> "VisibilityServer": ...


def normalize_tool_profile(raw: str) -> ToolProfile:
    profile = raw.strip().lower()
    if profile not in ALLOWED_TOOL_PROFILES:
        allowed = ", ".join(sorted(ALLOWED_TOOL_PROFILES))
        raise ValueError(f"tool_profile must be one of: {allowed}. Got {raw!r}.")
    return cast(ToolProfile, profile)


def tools_for_profile(profile: str) -> frozenset[str]:
    normalized = normalize_tool_profile(profile)
    return frozenset(
        name for name, entry in TOOL_CATALOG.items() if normalized in entry.profiles
    )


def tags_for_profile(profile: str) -> set[str]:
    normalized = normalize_tool_profile(profile)
    return {f"profile:{normalized}"}


def apply_tool_profile(mcp: VisibilityServer, profile: str) -> VisibilityServer:
    normalized = normalize_tool_profile(profile)
    mcp.enable(tags=tags_for_profile(normalized), only=True, components={"tool"})
    if normalized in {"default", "media", "diagnostic"}:
        mcp.disable(tags={"tool:experimental"}, components={"tool"})
    return mcp
