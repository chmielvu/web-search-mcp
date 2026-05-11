"""Provider configuration with mode-based selection logic.

ProviderMode controls when a provider fires:
- ALWAYS: Free providers (SearXNG, DDG) that always fire
- CONDITIONAL: Paid providers that only fire when explicitly requested by caller
- NEVER: Disabled providers that never fire even if configured
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class ProviderMode(Enum):
    """Provider availability mode."""
    ALWAYS = "always"           # Always included in search (free providers)
    CONDITIONAL = "conditional"  # Only when explicitly requested by caller
    NEVER = "never"             # Disabled, never included


@dataclass
class ProviderConfig:
    """Configuration for a single search provider."""
    name: str
    mode: ProviderMode
    env_key: str  # Environment variable for API key/base URL
    search_fn: Callable[..., Any]  # search_X function
    is_free: bool = False  # True for free/self-hosted providers
    requires_key: bool = True  # False for SearXNG (uses base URL) and DDG (no key)
    extra_env_keys: tuple[str, ...] = ()

    def is_available(self) -> bool:
        """Check if provider has required credentials configured."""
        if self.mode == ProviderMode.NEVER:
            return False
        if not self.env_key:
            # DDG has no env key requirement
            return True
        if not os.environ.get(self.env_key, "").strip():
            return False
        return all(os.environ.get(key, "").strip() for key in self.extra_env_keys)

    def should_fire(self, caller_providers: list[str] | None = None) -> bool:
        """Determine if this provider should be used for current search.

        Args:
            caller_providers: Optional list of provider names explicitly requested by caller.
                When provided (including empty list), acts as an allow-list.
                Empty list [] means "no providers" - nothing fires.
                None means "use default mode-based selection".

        Returns:
            True if this provider should fire for this search
        """
        if self.mode == ProviderMode.NEVER:
            return False

        # When caller specifies explicit providers (including empty), treat as allow-list.
        # Only fire if this provider is in the caller's list AND is available.
        # Empty list [] -> allow-list with nothing allowed -> nothing fires.
        if caller_providers is not None:
            return self.name in caller_providers and self.is_available()

        # No explicit caller list (None) - use mode-based selection.
        if self.mode == ProviderMode.ALWAYS:
            return self.is_available()

        if self.mode == ProviderMode.CONDITIONAL:
            # Only fire when explicitly requested (but caller_providers was None)
            return False

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
    caller_providers: list[str] | None = None,
) -> list[ProviderConfig]:
    """Resolve which providers should fire for this search.

    Args:
        caller_providers: Optional list of provider names requested by caller

    Returns:
        List of ProviderConfig objects that should fire
    """
    active: list[ProviderConfig] = []
    for config in PROVIDER_REGISTRY.values():
        if config.should_fire(caller_providers):
            active.append(config)
    return active


def parse_provider_mode(env_val: str) -> ProviderMode | None:
    """Parse provider mode from environment variable value.

    Args:
        env_val: Environment variable value string

    Returns:
        ProviderMode if valid, None otherwise
    """
    val = env_val.strip().lower()
    if val == "always":
        return ProviderMode.ALWAYS
    if val == "conditional":
        return ProviderMode.CONDITIONAL
    if val == "never":
        return ProviderMode.NEVER
    return None
