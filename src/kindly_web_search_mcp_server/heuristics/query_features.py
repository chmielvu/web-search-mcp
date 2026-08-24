"""Deterministic query feature extraction for role-dialect query shaping."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .lang_detect import detect_lang
from .text_clean import clean_query
from .text_segment import segment_query

if TYPE_CHECKING:
    from ..search.understanding.models import QueryUnderstandingResult

# Shared with github provider — single source of truth for repo/org/user hints.
REPO_HINT_PATTERN = re.compile(r"\brepo:([^\s]+/[^\s]+)\b", re.I)
ORG_HINT_PATTERN = re.compile(r"\borg:([^\s]+)\b", re.I)
USER_HINT_PATTERN = re.compile(r"\buser:([^\s]+)\b", re.I)
BARE_REPO_PATTERN = re.compile(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\b")
_LANG_TOKEN_PATTERN = re.compile(r"\b(?:lang|language):([A-Za-z0-9+#.]+)\b", re.I)
_PATH_TOKEN_PATTERN = re.compile(r"\b(?:path|file):([^\s]+)\b", re.I)
_DOTTED_IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b")
_CAMEL = re.compile(r"\b[A-Za-z]+(?:[A-Z][a-z0-9]+)+\b")
_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_SLASH_REGEX = re.compile(r"/(?:\\.|[^/\\])+/")
_CODE_META = re.compile(r"[.*+?\[\]()]")

# GitHub display name / Sourcegraph lowercase style mapped later in augment.
_LANG_ALIASES: dict[str, str] = {
    "ts": "TypeScript",
    "typescript": "TypeScript",
    "py": "Python",
    "python": "Python",
    "golang": "Go",
    "go": "Go",
    "js": "JavaScript",
    "javascript": "JavaScript",
    "rust": "Rust",
    "java": "Java",
    "ruby": "Ruby",
    "c++": "C++",
    "cpp": "C++",
    "cxx": "C++",
    "c#": "C#",
    "csharp": "C#",
    "cs": "C#",
    "php": "PHP",
    "swift": "Swift",
    "kotlin": "Kotlin",
    "scala": "Scala",
    "r": "R",
    "shell": "Shell",
    "bash": "Shell",
    "zsh": "Shell",
    "html": "HTML",
    "css": "CSS",
    "sql": "SQL",
    "dart": "Dart",
    "elixir": "Elixir",
    "haskell": "Haskell",
    "lua": "Lua",
    "perl": "Perl",
    "objective-c": "Objective-C",
    "objc": "Objective-C",
}

_STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "how",
        "what",
        "with",
        "from",
        "that",
        "this",
        "into",
        "about",
        "when",
        "where",
        "which",
        "while",
        "your",
        "you",
        "are",
        "is",
        "was",
        "were",
        "been",
        "being",
        "have",
        "has",
        "had",
        "does",
        "did",
        "can",
        "could",
        "should",
        "would",
        "will",
        "just",
        "than",
        "then",
        "them",
        "they",
        "their",
        "there",
        "here",
        "some",
        "any",
        "all",
        "not",
        "but",
        "also",
        "using",
        "use",
        "used",
        "via",
        "per",
        "out",
        "over",
        "under",
        "between",
        "after",
        "before",
        "best",
        "way",
        "like",
        "need",
        "want",
        "please",
        "help",
        "find",
        "search",
        "look",
        "looking",
        "code",
        "example",
        "examples",
        "docs",
        "documentation",
        "github",
        "gitlab",
        "sourcegraph",
        "reddit",
        "hackernews",
        "hn",
    }
)

# Sourcegraph `lang:` uses lowercase language names.
SG_LANG_MAP: dict[str, str] = {
    "TypeScript": "typescript",
    "Python": "python",
    "Go": "go",
    "JavaScript": "javascript",
    "Rust": "rust",
    "Java": "java",
    "Ruby": "ruby",
    "C++": "c++",
    "C#": "c#",
    "PHP": "php",
    "Swift": "swift",
    "Kotlin": "kotlin",
    "Scala": "scala",
    "R": "r",
    "Shell": "shell",
    "HTML": "html",
    "CSS": "css",
    "SQL": "sql",
    "Dart": "dart",
    "Elixir": "elixir",
    "Haskell": "haskell",
    "Lua": "lua",
    "Perl": "perl",
    "Objective-C": "objective-c",
}


@dataclass(frozen=True, slots=True)
class QueryFeatures:
    raw: str
    cleaned: str
    intent: str
    preserved_terms: tuple[str, ...]
    domain_hints: tuple[str, ...]
    languages: tuple[str, ...]
    repo_slugs: tuple[str, ...]
    orgs: tuple[str, ...]
    path_hints: tuple[str, ...]
    symbolish_terms: tuple[str, ...]
    want_regexp: bool
    notes: tuple[str, ...]
    lang: str = ""
    segmented_variants: tuple[str, ...] = ()
    compared_entities: tuple[str, ...] = ()
    time_sensitivity: str = "none"
    should_decompose: bool = False


def _uniq(items: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = item.strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return tuple(out)


def _normalize_lang(token: str) -> str | None:
    key = token.strip().casefold()
    if not key:
        return None
    if key in _LANG_ALIASES:
        return _LANG_ALIASES[key]
    # Already a known canonical label
    for canon in _LANG_ALIASES.values():
        if canon.casefold() == key:
            return canon
    return None


def _langs_from_text(text: str, domain_hints: Sequence[str]) -> tuple[str, ...]:
    found: list[str] = []
    for match in _LANG_TOKEN_PATTERN.finditer(text):
        lang = _normalize_lang(match.group(1))
        if lang:
            found.append(lang)
    # Bare language words as whole tokens
    for token in re.findall(r"[A-Za-z0-9+#.]+", text):
        lang = _normalize_lang(token)
        if lang and token.casefold() in _LANG_ALIASES:
            found.append(lang)
    for hint in domain_hints:
        lang = _normalize_lang(hint)
        if lang:
            found.append(lang)
        # domain_hints may include "python", "typescript", etc.
        lower = hint.casefold()
        if lower in _LANG_ALIASES:
            found.append(_LANG_ALIASES[lower])
    return _uniq(found)


def _symbolish(text: str) -> tuple[str, ...]:
    found: list[str] = []
    for pattern in (_DOTTED_IDENT, _CAMEL, _SNAKE):
        for match in pattern.finditer(text):
            token = match.group(0)
            if len(token) < 3:
                continue
            if token.casefold() in _STOP:
                continue
            found.append(token)
    return _uniq(found)


def _want_regexp(text: str) -> bool:
    if "patternType:regexp" in text or "patterntype:regexp" in text.casefold():
        return True
    if _SLASH_REGEX.search(text):
        return True
    # Metacharacters inside a non-space token that looks code-like
    for token in text.split():
        if len(token) < 3:
            continue
        if token.startswith(
            ("http://", "https://", "repo:", "lang:", "language:", "path:", "file:")
        ):
            continue
        if _CODE_META.search(token) and any(c.isalpha() for c in token):
            return True
    return False


def build_query_features(
    query: str,
    *,
    understanding: QueryUnderstandingResult | Any | None = None,
    support_terms: Sequence[str] = (),
) -> QueryFeatures:
    raw = query or ""
    cleaned = clean_query(raw)
    notes: list[str] = []
    intent = "general"
    preserved: list[str] = []
    domain_hints: list[str] = []

    compared_entities = _uniq(getattr(understanding, "compared_entities", None) or [])
    ts_raw = getattr(understanding, "time_sensitivity", None)
    time_sensitivity = str(ts_raw) if ts_raw else "none"
    should_decompose = bool(getattr(understanding, "should_decompose", False) or False)
    if understanding is not None:
        intent_val = getattr(understanding, "intent", None)
        if intent_val is not None:
            intent = str(intent_val)
        preserved.extend(getattr(understanding, "preserved_terms", None) or [])
        domain_hints.extend(getattr(understanding, "domain_hints", None) or [])
        notes.append("understanding.merged")
    preserved.extend(support_terms or ())
    preserved_terms = _uniq(preserved)
    domain_hints_t = _uniq(domain_hints)

    lang = detect_lang(cleaned)
    segmented_variants: tuple[str, ...] = ()
    if lang:
        notes.append(f"lang.detected:{lang}")
    else:
        notes.append("lang.unknown")
    # Segment English queries and ambiguous ones. With the calibrated
    # confidence+margin gate in lang_detect, '' reliably means "too short or
    # ambiguous to trust" (true non-English queries score high-margin), so
    # attempting segmentation there is safe; wordninja leaves real words
    # unchanged, so only genuinely glued tokens produce a variant.
    if lang in ("en", ""):
        segmented = segment_query(cleaned)
        if segmented:
            segmented_variants = (segmented,)
            notes.append("segmented.glued")

    repo_slugs = _uniq(
        [*(REPO_HINT_PATTERN.findall(cleaned) or ()), *(BARE_REPO_PATTERN.findall(cleaned) or ())]
    )
    # Drop false positives that are clearly URLs paths already covered
    repo_slugs = tuple(
        s
        for s in repo_slugs
        if not s.casefold().startswith(("http/", "https/"))
        and "/" in s
        and not s.endswith((".com", ".org", ".io", ".dev"))
    )
    orgs = _uniq(ORG_HINT_PATTERN.findall(cleaned) + USER_HINT_PATTERN.findall(cleaned))
    path_hints = _uniq(_PATH_TOKEN_PATTERN.findall(cleaned))
    languages = _langs_from_text(cleaned, domain_hints_t)
    symbols = _symbolish(cleaned)
    want_re = _want_regexp(cleaned)

    if repo_slugs:
        notes.append("repos.detected")
    if languages:
        notes.append("languages.detected")
    if symbols:
        notes.append("symbols.detected")
    if want_re:
        notes.append("regexp.wanted")
    if not cleaned:
        notes.append("empty.cleaned")

    return QueryFeatures(
        raw=raw,
        cleaned=cleaned,
        intent=intent,
        preserved_terms=preserved_terms,
        domain_hints=domain_hints_t,
        languages=languages,
        repo_slugs=repo_slugs,
        path_hints=path_hints,
        orgs=orgs,
        compared_entities=compared_entities,
        time_sensitivity=time_sensitivity,
        should_decompose=should_decompose,
        symbolish_terms=symbols,
        want_regexp=want_re,
        notes=tuple(notes),
        lang=lang,
        segmented_variants=segmented_variants,
    )
