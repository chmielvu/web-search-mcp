"""Profile data model."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..intents import SearchIntent


@dataclass(frozen=True, slots=True)
class SearchProfile:
    name: SearchIntent
    parent: SearchIntent | None = None
    provider_weights: dict[str, float] = field(default_factory=dict)
    provider_names: tuple[str, ...] | None = None
    provider_arguments: dict[str, dict[str, object]] = field(default_factory=dict)
    search_options_overrides: dict[str, object] = field(default_factory=dict)
    prompt_family: str = "general"
