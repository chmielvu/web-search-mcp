"""Provider-specific option bundles."""

from __future__ import annotations

from dataclasses import dataclass, field

from .options import SearchOptions


@dataclass(frozen=True, slots=True)
class ProviderOptionBundle:
    provider_name: str
    search_options: SearchOptions | None = None
    fire: bool = True
    weight: float = 1.0
    arguments: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderOptionSet:
    bundles: dict[str, ProviderOptionBundle]

    def bundle_for(self, provider_name: str) -> ProviderOptionBundle | None:
        return self.bundles.get(provider_name)
