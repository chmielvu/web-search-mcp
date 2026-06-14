"""Provider configuration and selection logic."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable

from ..settings import settings
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

    def is_enabled(self) -> bool:
        """Check whether this provider is allowed by the settings master switch."""
        if not settings.providers_enabled:
            return False
        if self.name == "qdrant" and not settings.qdrant_search_enabled:
            return False
        return self.name not in settings.disabled_providers

    def should_fire(self, intent: SearchIntent = "general") -> bool:
        """Determine if this provider should fire for the given search intent."""
        if not self.is_enabled():
            return False

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
_SERP_PAID_RR_LOCK = threading.Lock()
_SERP_PAID_RR_CURSOR = 0


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


def resolve_provider_configs(
    provider_names: Iterable[str],
    *,
    intent: SearchIntent = "general",
) -> list[ProviderConfig]:
    """Resolve an explicit provider list while honoring settings and health."""
    active: list[ProviderConfig] = []
    for provider_name in provider_names:
        config = PROVIDER_REGISTRY.get(provider_name)
        if config is None:
            continue
        if config.should_fire(intent):
            active.append(config)
    return active


def select_serp_paid_configs(
    configs: Iterable[ProviderConfig],
    *,
    limit: int | None = None,
) -> list[ProviderConfig]:
    """Select a round-robin subset of eligible paid SERP providers.

    The input order is treated as the stable provider order. When there are
    more eligible paid providers than the configured limit, the selector
    advances a shared cursor so successive calls rotate the chosen subset.
    """

    serp_paid_configs = [
        config for config in configs if config.group == ProviderGroup.serp_paid
    ]
    if not serp_paid_configs:
        return []

    selection_limit = settings.serp_semaphore_limit if limit is None else limit
    if selection_limit <= 0:
        return []

    if len(serp_paid_configs) <= selection_limit:
        return serp_paid_configs

    global _SERP_PAID_RR_CURSOR
    with _SERP_PAID_RR_LOCK:
        start = _SERP_PAID_RR_CURSOR % len(serp_paid_configs)
        selected = [
            serp_paid_configs[(start + offset) % len(serp_paid_configs)]
            for offset in range(selection_limit)
        ]
        _SERP_PAID_RR_CURSOR = (start + selection_limit) % len(serp_paid_configs)
    return selected


class DiagnosisCategory(str, Enum):
    """Semantic category for a provider diagnosis.

    Only ``cooldown`` and ``disabled`` surface as user-visible warnings.
    ``unconfigured`` and ``intent_excluded`` are silent-by-design.
    """

    healthy = "healthy"
    disabled = "disabled"
    cooldown = "cooldown"
    unconfigured = "unconfigured"
    intent_excluded = "intent_excluded"


@dataclass
class ProviderDiagnosis:
    """Why a requested provider could not fire."""

    name: str
    available: bool
    reason: str
    category: DiagnosisCategory = DiagnosisCategory.healthy


def diagnose_providers(
    intent: SearchIntent = "general",
) -> list[ProviderDiagnosis]:
    """Check all registered providers and explain why each does or does not fire for the given intent."""
    from .provider_health import get_provider_health  # noqa: PLC0415

    diagnoses: list[ProviderDiagnosis] = []
    for name, config in PROVIDER_REGISTRY.items():
        if not config.is_enabled():
            if not settings.providers_enabled:
                reason = (
                    "Provider master switch is disabled via PROVIDERS_ENABLED=false."
                )
            else:
                reason = (
                    f"Provider '{name}' is disabled via DISABLED_PROVIDERS."
                )
            diagnoses.append(
                ProviderDiagnosis(
                    name=name,
                    available=False,
                    reason=reason,
                    category=DiagnosisCategory.disabled,
                )
            )
            continue

        # SERP providers (free + serp_paid) are eligible subject to health/availability.
        # Paid SERP providers may still be rotated out later by the execution plan.
        is_serp = config.group in (ProviderGroup.free, ProviderGroup.serp_paid)

        if is_serp:
            if not get_provider_health().is_healthy(name):
                diagnoses.append(
                    ProviderDiagnosis(
                        name=name,
                        available=False,
                        reason=f"Provider '{name}' is in health cooldown after repeated failures.",
                        category=DiagnosisCategory.cooldown,
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
                        category=DiagnosisCategory.unconfigured,
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
                        category=DiagnosisCategory.intent_excluded,
                    )
                )
                continue
            if not get_provider_health().is_healthy(name):
                diagnoses.append(
                    ProviderDiagnosis(
                        name=name,
                        available=False,
                        reason=f"Provider '{name}' is in health cooldown after repeated failures.",
                        category=DiagnosisCategory.cooldown,
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
                        category=DiagnosisCategory.unconfigured,
                    )
                )
                continue
            diagnoses.append(ProviderDiagnosis(name=name, available=True, reason="ok"))

    return diagnoses
