"""Provider configuration and selection logic."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable

from ..settings import settings


class ProviderGroup(Enum):
    """Group a search provider belongs to for routing and concurrency."""

    free = "free"
    paid_serp = "paid_serp"
    specialized = "specialized"


@dataclass
class ProviderConfig:
    """Configuration for a single search provider."""

    name: str
    env_key: str
    search_fn: Callable[..., Any]
    group: ProviderGroup = ProviderGroup.specialized
    requires_key: bool = True
    extra_env_keys: tuple[str, ...] = ()

    @property
    def is_free(self) -> bool:
        return self.group == ProviderGroup.free

    def is_available(self) -> bool:
        if not self.env_key:
            return True
        if not os.environ.get(self.env_key, "").strip():
            return False
        return all(os.environ.get(key, "").strip() for key in self.extra_env_keys)

    def is_enabled(self) -> bool:
        if not settings.providers_enabled:
            return False
        return self.name not in settings.disabled_providers


PROVIDER_REGISTRY: dict[str, ProviderConfig] = {}
_SERP_PAID_RR_LOCK = threading.Lock()
_SERP_PAID_RR_CURSOR = 0


def register_provider(config: ProviderConfig) -> None:
    PROVIDER_REGISTRY[config.name] = config


def get_provider_configs() -> dict[str, ProviderConfig]:
    return PROVIDER_REGISTRY.copy()


def resolve_provider_configs(provider_names: Iterable[str]) -> list[ProviderConfig]:
    active: list[ProviderConfig] = []

    for provider_name in provider_names:
        config = PROVIDER_REGISTRY.get(provider_name)
        if config is None:
            continue
        if not config.is_enabled():
            continue
        active.append(config)
    return active


def select_paid_serp_configs(
    configs: Iterable[ProviderConfig],
    *,
    limit: int = 2,
) -> list[ProviderConfig]:
    paid_serp_configs = [
        config for config in configs if config.group == ProviderGroup.paid_serp
    ]
    if not paid_serp_configs or limit <= 0:
        return []
    if len(paid_serp_configs) <= limit:
        return paid_serp_configs

    global _SERP_PAID_RR_CURSOR
    with _SERP_PAID_RR_LOCK:
        start = _SERP_PAID_RR_CURSOR % len(paid_serp_configs)
        selected = [
            paid_serp_configs[(start + offset) % len(paid_serp_configs)]
            for offset in range(limit)
        ]
        _SERP_PAID_RR_CURSOR = (start + limit) % len(paid_serp_configs)
    return selected


class DiagnosisCategory(str, Enum):
    healthy = "healthy"
    disabled = "disabled"
    cooldown = "cooldown"
    unconfigured = "unconfigured"


@dataclass
class ProviderDiagnosis:
    name: str
    available: bool
    reason: str
    category: DiagnosisCategory = DiagnosisCategory.healthy


def diagnose_providers() -> list[ProviderDiagnosis]:
    diagnoses: list[ProviderDiagnosis] = []
    for name, config in PROVIDER_REGISTRY.items():
        if not config.is_enabled():
            if not settings.providers_enabled:
                reason = "Provider master switch is disabled via PROVIDERS_ENABLED=false."
            else:
                reason = f"Provider '{name}' is disabled via DISABLED_PROVIDERS."
            diagnoses.append(
                ProviderDiagnosis(
                    name=name,
                    available=False,
                    reason=reason,
                    category=DiagnosisCategory.disabled,
                )
            )
            continue

        if not config.is_available():
            env_hint = f" Set {config.env_key} environment variable." if config.env_key else ""
            reason = f"Provider '{name}' is not configured (missing credentials).{env_hint}"
            diagnoses.append(
                ProviderDiagnosis(
                    name=name,
                    available=False,
                    reason=reason,
                    category=DiagnosisCategory.unconfigured,
                )
            )
            continue

        diagnoses.append(ProviderDiagnosis(name=name, available=True, reason="ok"))

    return diagnoses
