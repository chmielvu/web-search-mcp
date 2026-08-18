"""Shared Brave request helpers.

Centralises the standard-key lookup, auth headers, query bound, and freshness
translation used by ``brave.py`` (LLM Context) and ``brave_news.py``. Keeping
these in one place prevents request-invariant drift across the Brave surfaces
while leaving the public provider functions in their own modules.
"""

from __future__ import annotations

from .base import ProviderRequestError

from ...settings import settings, get_env_value


class BraveError(ProviderRequestError):
    """Base error for Brave provider failures."""


class BraveConfigError(BraveError):
    """Raised when the standard Brave API key is missing."""


BRAVE_LLM_CONTEXT_URL = "https://api.search.brave.com/res/v1/llm/context"
BRAVE_NEWS_URL = "https://api.search.brave.com/res/v1/news/search"


def _get_brave_api_key() -> str:
    """Return the standard Brave API key or raise. Never falls back to Suggest key."""
    api_key = get_env_value("BRAVE_API_KEY", settings.brave_api_key).strip()
    if not api_key:
        raise BraveConfigError("BRAVE_API_KEY is not set. Configure it in your runtime settings.")
    return api_key


def _brave_headers(api_key: str) -> dict[str, str]:
    """Standard Brave auth/accept headers for all Brave endpoints."""
    return {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }


def _bound_brave_query(query: str) -> str:
    """Cap the outbound query to Brave's 400-char / 50-word limit.

    Provider-local: this never alters the user-visible rewrite query or literal
    syntax sent to the free/keyword/neural branches; it only bounds what Brave
    receives. Autosuggest keeps its own independent 200-char bound elsewhere.
    """
    words = query.split()
    if len(words) > 50:
        query = " ".join(words[:50])
    if len(query) > 400:
        query = query[:400]
    return query


_VALID_FRESHNESS_TOKENS: frozenset[str] = frozenset({"pd", "pw", "pm", "py"})
_BRAVE_FRESHNESS_MAP: dict[str, str] = {
    "day": "pd",
    "week": "pw",
    "month": "pm",
    "year": "py",
}


def translate_brave_freshness(value: str | None) -> str | None:
    """Map an intent freshness word to a Brave wire token.

    Passes through already-valid Brave tokens (``pd``/``pw``/``pm``/``py``) and
    custom ``YYYY-MM-DDtoYYYY-MM-DD`` ranges. Raises ``BraveError`` for anything
    else so a bad value is never silently turned into a bogus API parameter.
    """
    if not value:
        return None
    normalized = value.strip().lower()
    if normalized in _VALID_FRESHNESS_TOKENS:
        return normalized
    if normalized in _BRAVE_FRESHNESS_MAP:
        return _BRAVE_FRESHNESS_MAP[normalized]
    if "to" in normalized:
        return normalized
    raise BraveError(f"Unsupported Brave freshness value: {value!r}")
