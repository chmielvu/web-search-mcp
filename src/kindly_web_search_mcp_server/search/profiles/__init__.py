"""Search profile registry."""

from .defaults import build_default_profiles
from .models import SearchProfile
from .registry import get_profile, resolve_profile_name, resolve_profiles
from .resolve import resolve_search_profile

__all__ = [
    "SearchProfile",
    "build_default_profiles",
    "get_profile",
    "resolve_profile_name",
    "resolve_profiles",
    "resolve_search_profile",
]
