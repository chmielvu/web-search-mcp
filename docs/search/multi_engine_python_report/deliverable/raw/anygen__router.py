"""Mode → providers routing with broadened coverage for maximum recall."""
from __future__ import annotations

from hsearch.config import configured_providers

# Per-mode provider preference order. Real selection is intersected with what's configured.
# Broadened vs original: each mode now includes more providers for better recall.
MODE_MAP: dict[str, list[str]] = {
    "default": ["tavily", "brave"],
    "news": ["brave", "serper", "tavily"],
    "academic": ["exa", "serper"],
    "code": ["exa", "brave", "serper"],
    "general": ["tavily", "brave", "serper"],
    "realtime": ["serper", "brave"],
    "shopping": ["serper", "brave"],
    "video": ["serper", "brave"],
    "images": ["serper", "brave"],
    "places": ["serper", "brave"],
    "answer": ["tavily", "brave"],
    "deep": ["exa", "tavily"],
    "fast": ["exa", "tavily"],
    "company": ["exa"],
    "finance": ["tavily", "serper", "brave"],
    "recall": ["exa", "tavily", "brave", "serper", "firecrawl", "jina"],
    "context": ["brave"],
    # v1.0.0 — Exa-only: contents.context returns ONE pre-assembled LLM-ready
    # context string. Single provider on purpose; merging context blobs across
    # providers would defeat the "one clean string" contract.
    "rag": ["exa"],
}

# Fallback providers per provider — used when primary provider fails.
FALLBACK_MAP: dict[str, list[str]] = {
    "tavily": ["brave", "serper"],
    "brave": ["serper", "tavily"],
    "serper": ["brave", "tavily"],
    "exa": ["tavily", "brave"],
    "firecrawl": ["jina", "brave"],
    "jina": ["firecrawl", "brave"],
}

ALL_MODES: tuple[str, ...] = tuple(MODE_MAP)


def providers_for_mode(mode: str | None) -> list[str]:
    """Return the configured providers chosen for a given mode (in priority order).

    Falls back to the first configured provider if none of the preferred ones have keys.
    """
    key = (mode or "default").lower()
    pref = MODE_MAP.get(key, MODE_MAP["default"])
    available = set(configured_providers())
    chosen = [p for p in pref if p in available]
    if chosen:
        return chosen
    # Fallback: pick any configured provider deterministically.
    fallback = configured_providers()
    return fallback[:1]


def fallback_providers(failed: str) -> list[str]:
    """Return fallback providers for a failed provider, filtered to what's configured."""
    available = set(configured_providers())
    candidates = FALLBACK_MAP.get(failed, [])
    return [p for p in candidates if p in available]
