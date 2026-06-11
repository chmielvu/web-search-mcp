"""Provider configuration and selection logic."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ProviderConfig:
    """Configuration for a single search provider."""

    name: str
    env_key: str  # Environment variable for API key/base URL
    search_fn: Callable[..., Any]  # search_X function
    is_free: bool = False  # True for free/self-hosted providers
    requires_key: bool = True  # False for SearXNG (uses base URL) and DDG (no key)
    extra_env_keys: tuple[str, ...] = ()

    def is_available(self) -> bool:
        """Check if provider has required credentials configured."""
        if not self.env_key:
            # DDG has no env key requirement.
            return True
        if not os.environ.get(self.env_key, "").strip():
            return False
        return all(os.environ.get(key, "").strip() for key in self.extra_env_keys)

    def should_fire(self, caller_providers: list[str] | None = None) -> bool:
        """Determine if this provider should be used for current search."""
        # Health check: skip providers that are in cooldown
        # (lazy import to avoid circular dependency)
        from .provider_health import get_provider_health  # noqa: PLC0415

        if not get_provider_health().is_healthy(self.name):
            return False

        if not self.is_available():
            return False

        # When caller specifies explicit providers (including empty), treat as allow-list.
        # Empty list [] -> allow-list with nothing allowed -> nothing fires.
        if caller_providers is not None:
            return self.name in caller_providers

        return True


# Provider registry
PROVIDER_REGISTRY: dict[str, ProviderConfig] = {}


def register_provider(config: ProviderConfig) -> None:
    """Register a provider configuration."""
    PROVIDER_REGISTRY[config.name] = config


def get_provider_configs() -> dict[str, ProviderConfig]:
    """Get all registered provider configs."""
    return PROVIDER_REGISTRY.copy()


def resolve_providers_for_search(
    caller_providers: list[str] | None = None,
) -> list[ProviderConfig]:
    """Resolve which providers should fire for this search."""
    active: list[ProviderConfig] = []
    for config in PROVIDER_REGISTRY.values():
        if config.should_fire(caller_providers):
            active.append(config)
    return active


@dataclass
class ProviderDiagnosis:
    """Why a requested provider could not fire."""

    name: str
    available: bool
    reason: str  # e.g., "missing API key", "provider health cooldown"


def diagnose_providers(
    caller_providers: list[str] | None = None,
) -> list[ProviderDiagnosis]:
    """Check all requested providers and explain why each cannot fire."""
    if not caller_providers:
        return []

    from .provider_health import get_provider_health  # noqa: PLC0415

    diagnoses: list[ProviderDiagnosis] = []
    for name in caller_providers:
        config = PROVIDER_REGISTRY.get(name)
        if config is None:
            diagnoses.append(
                ProviderDiagnosis(
                    name=name,
                    available=False,
                    reason=f"Unknown provider '{name}'. Available: {sorted(PROVIDER_REGISTRY.keys())}",
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
