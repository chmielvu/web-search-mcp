from __future__ import annotations

import yake

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


def _yake_extract(text: str, max_phrases: int = 8) -> list[str]:
    """Synchronously extract ranked, meaningful phrases with YAKE.

    YAKE is pure Python — no native extensions, no thread-safety issues,
    no NLTK/scipy dependency. Lower score = more important keyword.
    """
    if not text.strip():
        return []

    extractor = yake.KeywordExtractor(
        lan="en",
        n=_MAX_PHRASE_WORDS,
        dedupLim=0.9,
        top=max_phrases * 2,  # oversample so filtering still yields max_phrases
    )
    keywords = extractor.extract_keywords(text)

    filtered: list[str] = []
    seen: set[str] = set()
    for phrase, _score in keywords:
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
    # YAKE is CPU-bound, so still run in executor — but now it's pure Python,
    # no native C extensions to corrupt across threads.
    import asyncio

    loop = asyncio.get_running_loop()
    raw_phrases = await loop.run_in_executor(None, _yake_extract, research_goal, max_terms)
    return [_restore_casing(phrase, research_goal) for phrase in raw_phrases]
