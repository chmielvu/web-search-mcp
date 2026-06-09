"""Default profile definitions for 0.2."""

from __future__ import annotations

from ..intents import SearchIntent
from ...settings import settings
from .models import SearchProfile


def build_default_profiles() -> dict[SearchIntent, SearchProfile]:
    base = SearchProfile(
        name="general",
        provider_weights=dict(settings.rrf_provider_weights),
        prompt_family="worker",
    )
    return {
        "general": base,
        "ai_coding": SearchProfile(name="ai_coding", parent="general"),
        "digital_humanities": SearchProfile(name="digital_humanities", parent="general"),
        "comparison": SearchProfile(name="comparison", parent="general"),
    }
