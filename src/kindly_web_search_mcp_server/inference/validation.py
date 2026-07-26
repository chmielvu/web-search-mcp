"""Catalog validation and safe inspection helpers for the inference subsystem."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass

from .chain import get_chain, list_chains
from .registry import (
    get_model,
    get_provider,
    list_model_specs,
    list_models,
    list_providers,
)
from .types import _API_KEY_ENV_ALIASES


@dataclass(frozen=True, slots=True)
class CatalogIssue:
    severity: str
    code: str
    message: str
    chain: str | None = None
    model: str | None = None
    provider_key: str | None = None
    api_key_env: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True, slots=True)
class CatalogValidationReport:
    ok: bool
    issue_count: int
    error_count: int
    warning_count: int
    chain_count: int
    model_count: int
    provider_adapter_count: int
    issues: tuple[CatalogIssue, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "issue_count": self.issue_count,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "chain_count": self.chain_count,
            "model_count": self.model_count,
            "provider_adapter_count": self.provider_adapter_count,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _api_key_configured(api_key_env: str) -> bool:
    env_names = (api_key_env, *_API_KEY_ENV_ALIASES.get(api_key_env, ()))
    return any(bool(os.environ.get(name, "").strip()) for name in env_names)


def describe_chain(name: str) -> dict[str, object]:
    """Return a secret-safe description of one registered chain."""
    chain = get_chain(name)
    steps: list[dict[str, object]] = []
    for index, spec_id in enumerate(chain.model_spec_ids, start=1):
        try:
            spec = get_model(spec_id)
        except Exception as exc:  # noqa: BLE001 - report broken refs without crashing inspection
            steps.append(
                {
                    "position": index,
                    "spec_id": spec_id,
                    "ok": False,
                    "error": str(exc),
                }
            )
            continue

        provider_key = spec_id.split("@", 1)[1] if "@" in spec_id else spec.provider
        adapter_ok = True
        adapter_error: str | None = None
        try:
            get_provider(spec.provider)
        except Exception as exc:  # noqa: BLE001
            adapter_ok = False
            adapter_error = str(exc)

        key_configured = _api_key_configured(spec.api_key_env)
        steps.append(
            {
                "position": index,
                "spec_id": spec_id,
                "ok": adapter_ok and bool(spec.model_id.strip()) and key_configured,
                "canonical_id": spec_id.split("@", 1)[0] if "@" in spec_id else spec.model_id,
                "provider": spec.provider,
                "provider_key": provider_key,
                "model_id": spec.model_id,
                "api_key_env": spec.api_key_env,
                "api_key_configured": key_configured,
                "base_url": spec.base_url,
                "default_timeout": spec.default_timeout,
                "adapter_registered": adapter_ok,
                "adapter_error": adapter_error,
            }
        )

    return {
        "name": chain.name,
        "length": len(steps),
        "steps": steps,
    }


def describe_catalog() -> dict[str, object]:
    """Return a secret-safe overview of models, providers, and chains."""
    models = []
    for model in list_models():
        models.append(
            {
                "canonical_id": model.canonical_id,
                "display_name": model.display_name,
                "description": model.description,
                "capabilities": sorted(cap.value for cap in model.capabilities),
                "providers": [
                    {
                        "provider_key": provider_key,
                        "model_id": config.model_id,
                        "api_key_env": config.api_key_env,
                        "api_key_configured": _api_key_configured(config.api_key_env),
                        "base_url": config.base_url,
                        "default_timeout": config.default_timeout,
                    }
                    for provider_key, config in model.provider_configs.items()
                ],
            }
        )

    return {
        "models": models,
        "provider_adapters": list_providers(),
        "chains": [describe_chain(name) for name in list_chains()],
        "model_spec_count": len(list_model_specs()),
    }


def validate_catalog() -> CatalogValidationReport:
    """Validate registered models, adapters, and chain references."""
    issues: list[CatalogIssue] = []

    provider_adapters = list_providers()
    if not provider_adapters:
        issues.append(
            CatalogIssue(
                severity="error",
                code="no_provider_adapters",
                message=(
                    "No provider adapters are registered. Import "
                    "`kindly_web_search_mcp_server.inference.adapters` during package bootstrap."
                ),
            )
        )

    for model in list_models():
        if not model.provider_configs:
            issues.append(
                CatalogIssue(
                    severity="error",
                    code="model_missing_providers",
                    message=f"Model '{model.canonical_id}' has no provider configurations.",
                    model=model.canonical_id,
                )
            )
        for provider_key, config in model.provider_configs.items():
            if not config.model_id or not config.model_id.strip():
                issues.append(
                    CatalogIssue(
                        severity="error",
                        code="empty_model_id",
                        message=(
                            f"Model '{model.canonical_id}' provider '{provider_key}' "
                            "has an empty model_id."
                        ),
                        model=model.canonical_id,
                        provider_key=provider_key,
                    )
                )
            if not config.api_key_env or not config.api_key_env.strip():
                issues.append(
                    CatalogIssue(
                        severity="error",
                        code="empty_api_key_env",
                        message=(
                            f"Model '{model.canonical_id}' provider '{provider_key}' "
                            "has an empty api_key_env."
                        ),
                        model=model.canonical_id,
                        provider_key=provider_key,
                    )
                )
            else:
                try:
                    get_provider(provider_key)
                except KeyError as exc:
                    issues.append(
                        CatalogIssue(
                            severity="error",
                            code="missing_provider_adapter",
                            message=str(exc),
                            model=model.canonical_id,
                            provider_key=provider_key,
                            api_key_env=config.api_key_env,
                        )
                    )
                if not _api_key_configured(config.api_key_env):
                    issues.append(
                        CatalogIssue(
                            severity="warning",
                            code="missing_api_key",
                            message=(
                                f"Environment variable '{config.api_key_env}' is not set "
                                f"for {model.canonical_id}@{provider_key}."
                            ),
                            model=model.canonical_id,
                            provider_key=provider_key,
                            api_key_env=config.api_key_env,
                        )
                    )

    chain_names = list_chains()
    if not chain_names:
        issues.append(
            CatalogIssue(
                severity="error",
                code="no_chains",
                message="No inference chains are registered.",
            )
        )

    for chain_name in chain_names:
        chain = get_chain(chain_name)
        if not chain.model_spec_ids:
            issues.append(
                CatalogIssue(
                    severity="error",
                    code="empty_chain",
                    message=f"Chain '{chain_name}' has no model references.",
                    chain=chain_name,
                )
            )
            continue
        for spec_id in chain.model_spec_ids:
            try:
                spec = get_model(spec_id)
            except Exception as exc:  # noqa: BLE001
                issues.append(
                    CatalogIssue(
                        severity="error",
                        code="unresolved_chain_ref",
                        message=f"Chain '{chain_name}' reference '{spec_id}' is invalid: {exc}",
                        chain=chain_name,
                        model=spec_id.split("@", 1)[0] if "@" in spec_id else None,
                        provider_key=spec_id.split("@", 1)[1] if "@" in spec_id else None,
                    )
                )
                continue
            try:
                get_provider(spec.provider)
            except KeyError as exc:
                issues.append(
                    CatalogIssue(
                        severity="error",
                        code="chain_missing_adapter",
                        message=str(exc),
                        chain=chain_name,
                        model=spec_id.split("@", 1)[0],
                        provider_key=spec_id.split("@", 1)[1],
                        api_key_env=spec.api_key_env,
                    )
                )

    error_count = sum(1 for issue in issues if issue.severity == "error")
    warning_count = sum(1 for issue in issues if issue.severity == "warning")
    return CatalogValidationReport(
        ok=error_count == 0,
        issue_count=len(issues),
        error_count=error_count,
        warning_count=warning_count,
        chain_count=len(chain_names),
        model_count=len(list_models()),
        provider_adapter_count=len(provider_adapters),
        issues=tuple(issues),
    )
