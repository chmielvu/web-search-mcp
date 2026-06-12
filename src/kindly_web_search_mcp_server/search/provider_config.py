"""Provider configuration and selection logic."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from .intents import INTENT_PROVIDERS, SearchIntent


class ProviderGroup(Enum):
    """Group a search provider belongs to for routing/priority decisions."""

    free = "free"
    serp_paid = "serp_paid"
    other = "other"


@dataclass
class ProviderConfig:
    """Configuration for a single search provider."""

    name: str
    env_key: str  # Environment variable for API key/base URL
    search_fn: Callable[..., Any]  # search_X function
    group: ProviderGroup = ProviderGroup.other  # Provider group for routing
    requires_key: bool = True  # False for SearXNG (uses base URL) and DDG (no key)
    extra_env_keys: tuple[str, ...] = ()

    @property
    def is_free(self) -> bool:
        """Backward-compatible check: True if this provider is in the free group."""
        return self.group == ProviderGroup.free

    def is_available(self) -> bool:
        """Check if provider has required credentials configured."""
        if not self.env_key:
            # DDG has no env key requirement.
            return True
        if not os.environ.get(self.env_key, "").strip():
            return False
        return all(os.environ.get(key, "").strip() for key in self.extra_env_keys)

    def should_fire(self, intent: SearchIntent = "general") -> bool:
        """Determine if this provider should fire for the given search intent."""
        # Health check: skip providers that are in cooldown
        # (lazy import to avoid circular dependency)
        from .provider_health import get_provider_health  # noqa: PLC0415

        if not get_provider_health().is_healthy(self.name):
            return False

        if not self.is_available():
            return False

        # Group logic
        if self.group in (ProviderGroup.free, ProviderGroup.serp_paid):
            return True
        if self.group == ProviderGroup.other:
            return self.name in INTENT_PROVIDERS.get(intent, [])
        return False


# Provider registry
PROVIDER_REGISTRY: dict[str, ProviderConfig] = {}


def register_provider(config: ProviderConfig) -> None:
    """Register a provider configuration."""
    PROVIDER_REGISTRY[config.name] = config


def get_provider_configs() -> dict[str, ProviderConfig]:
    """Get all registered provider configs."""
    return PROVIDER_REGISTRY.copy()


def resolve_providers_for_search(
    intent: SearchIntent = "general",
) -> list[ProviderConfig]:
    """Resolve which providers should fire for this search intent."""
    active: list[ProviderConfig] = []
    for config in PROVIDER_REGISTRY.values():
        if config.should_fire(intent):
            active.append(config)
    return active


@dataclass
class ProviderDiagnosis:
    """Why a requested provider could not fire."""

    name: str
    available: bool
    reason: str  # e.g., "missing API key", "provider health cooldown"


def diagnose_providers(
    intent: SearchIntent = "general",
) -> list[ProviderDiagnosis]:
    """Check all registered providers and explain why each does or does not fire for the given intent."""
    from .provider_health import get_provider_health  # noqa: PLC0415

    diagnoses: list[ProviderDiagnosis] = []
    for name, config in PROVIDER_REGISTRY.items():
        # SERP providers (free + serp_paid) always fire subject to health/availability
        is_serp = config.group in (ProviderGroup.free, ProviderGroup.serp_paid)

        if is_serp:
            if not get_provider_health().is_healthy(name):
                diagnoses.append(
                    ProviderDiagnosis(
                        name=name,
                        available=False,
                        reason=f"Provider '{name}' is in health cooldown after repeated failures.",
                    )
                )
                continue
            if not config.is_available():
                env_hint = (
                    f" Set {config.env_key} environment variable." if config.env_key else ""
                )
                diagnoses.append(
                    ProviderDiagnosis(
                        name=name,
                        available=False,
                        reason=f"Provider '{name}' is not configured (missing credentials).{env_hint}",
                    )
                )
                continue
            diagnoses.append(ProviderDiagnosis(name=name, available=True, reason="ok"))
        else:
            # Other providers: fire only if in INTENT_PROVIDERS for this intent
            intent_providers = INTENT_PROVIDERS.get(intent, [])
            if name not in intent_providers:
                diagnoses.append(
                    ProviderDiagnosis(
                        name=name,
                        available=False,
                        reason=f"Provider '{name}' is not in INTENT_PROVIDERS[{intent!r}]. "
                        f"Intent providers: {intent_providers}",
                    )
                )
                continue
            if not get_provider_health().is_healthy(name):
                diagnoses.append(
                    ProviderDiagnosis(
                        name=name,
                        available=False,
                        reason=f"Provider '{name}' is in health cooldown after repeated failures.",
                    )
                )
                continue
            if not config.is_available():
                env_hint = (
                    f" Set {config.env_key} environment variable." if config.env_key else ""
                )
                diagnoses.append(
                    ProviderDiagnosis(
                        name=name,
                        available=False,
                        reason=f"Provider '{name}' is not configured (missing credentials).{env_hint}",
                    )
                )
                continue
            diagnoses.append(ProviderDiagnosis(name=name, available=True, reason="ok"))

    return diagnoses
