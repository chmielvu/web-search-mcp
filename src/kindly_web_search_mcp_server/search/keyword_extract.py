from __future__ import annotations

import asyncio
import re


_MAX_PHRASE_WORDS = 4
_NOISE_VERBS = frozenset(
    {
        "want",
        "need",
        "find",
        "get",
        "look",
        "search",
        "compare",
        "show",
        "tell",
        "give",
        "make",
        "help",
        "learn",
        "know",
    }
)


def _rake_extract(text: str, max_phrases: int = 8) -> list[str]:
    """Synchronously extract ranked, meaningful phrases with RAKE."""
    from rake_nltk import Rake

    if not text.strip():
        return []
    rake = Rake()
    rake.extract_keywords_from_text(text)
    ranked_phrases = list(rake.get_ranked_phrases())
    title_phrases = re.findall(
        r"\b(?:[A-Z][A-Za-z0-9.+-]*\s+){1,3}[A-Z][A-Za-z0-9.+-]*\b",
        text,
    )
    candidates = ranked_phrases + title_phrases
    filtered: list[str] = []
    seen: set[str] = set()
    for phrase in candidates:
        words = phrase.split()
        if not words:
            continue
        phrase = " ".join(words[:_MAX_PHRASE_WORDS])
        key = phrase.casefold()
        if key in seen or all(word.lower().rstrip("sing") in _NOISE_VERBS for word in words):
            continue
        seen.add(key)
        filtered.append(phrase)
        if len(filtered) >= max_phrases:
            break
    return filtered


def _restore_casing(phrase: str, source: str) -> str:
    """Best-effort restore original casing from source text."""
    lower = phrase.lower()
    source_lower = source.lower()
    start = source_lower.find(lower)
    return source[start : start + len(phrase)] if start >= 0 else phrase


async def extract_support_terms(
    research_goal: str,
    *,
    max_terms: int = 8,
) -> list[str]:
    """Extract key phrases without blocking the event loop."""
    loop = asyncio.get_running_loop()
    raw_phrases = await loop.run_in_executor(None, _rake_extract, research_goal, max_terms)
    return [_restore_casing(phrase, research_goal) for phrase in raw_phrases]

