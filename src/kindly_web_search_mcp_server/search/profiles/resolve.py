"""Search profile resolver."""

from __future__ import annotations

from ..intents import normalize_intent
from .models import SearchProfile
from .registry import get_profile


def resolve_search_profile(intent: str | None) -> SearchProfile:
    return get_profile(normalize_intent(intent))
