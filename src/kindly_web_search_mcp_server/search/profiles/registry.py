"""Search profile resolution with inheritance."""

from __future__ import annotations

from dataclasses import replace

from ..intents import SearchIntent, normalize_intent
from ..options import SearchOptions
from .defaults import build_default_profiles
from .models import SearchProfile


_PROFILES: dict[SearchIntent, SearchProfile] = build_default_profiles()


def resolve_profile_name(intent: str | None) -> SearchIntent:
    return normalize_intent(intent)


def get_profile(name: SearchIntent) -> SearchProfile:
    profile = _PROFILES[name]
    if profile.parent is None:
        return profile
    parent = _PROFILES[profile.parent]
    provider_weights = dict(parent.provider_weights)
    provider_weights.update(profile.provider_weights)
    provider_names = profile.provider_names if profile.provider_names is not None else parent.provider_names
    provider_arguments = dict(parent.provider_arguments)
    provider_arguments.update(profile.provider_arguments)
    search_options_overrides = dict(parent.search_options_overrides)
    search_options_overrides.update(profile.search_options_overrides)
    return SearchProfile(
        name=profile.name,
        parent=profile.parent,
        provider_weights=provider_weights,
        provider_names=provider_names,
        provider_arguments=provider_arguments,
        search_options_overrides=search_options_overrides,
        prompt_family=profile.prompt_family or parent.prompt_family,
    )


def resolve_profiles() -> dict[SearchIntent, SearchProfile]:
    return {name: get_profile(name) for name in _PROFILES}


def apply_profile_search_options(
    search_options: SearchOptions | None,
    profile: SearchProfile,
) -> SearchOptions | None:
    if search_options is None and not profile.search_options_overrides:
        return None
    base = search_options or SearchOptions()
    if not profile.search_options_overrides:
        return base
    return replace(base, **profile.search_options_overrides).validate()
