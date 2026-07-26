"""Provider-dialect query augmentation (no network)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .query_features import (
    BARE_REPO_PATTERN,
    REPO_HINT_PATTERN,
    SG_LANG_MAP,
    QueryFeatures,
    build_query_features,
)
from .text_clean import clean_query

SPECIALIZED_AUGMENT_PROVIDERS = frozenset(
    {"github", "sourcegraph", "gitlab", "hackernews", "reddit"}
)

_CODE_OPS = re.compile(
    r"\b(?:repo|org|user|lang|language|path|file|patternType|select|type|is|in):[^\s]+",
    re.I,
)

_GH_CODE_MARKERS = (
    "github",
    "issue",
    "issues",
    "discussion",
    "discussions",
    "pr",
    "pull",
    "repo",
)


@dataclass(frozen=True, slots=True)
class AugmentResult:
    query: str
    changed: bool
    rules_applied: tuple[str, ...]
    metadata: tuple[tuple[str, str], ...] = ()


def _result(
    original: str,
    shaped: str,
    rules: list[str],
    metadata: dict[str, str] | None = None,
) -> AugmentResult:
    shaped = " ".join(shaped.split()).strip()
    if not shaped:
        shaped = clean_query(original) or original
    rules_t = tuple(dict.fromkeys(rules))  # stable unique
    return AugmentResult(
        query=shaped,
        changed=shaped != (original or ""),
        rules_applied=rules_t,
        metadata=tuple(sorted((metadata or {}).items())),
    )


def _has_qualifier(query: str, name: str) -> bool:
    return re.search(rf"\b{re.escape(name)}:", query, re.I) is not None


def _augment_github(query: str, features: QueryFeatures) -> AugmentResult:
    rules: list[str] = []
    body = clean_query(query) or features.cleaned
    if body != (query or "").strip():
        rules.append("clean.query")

    # Keep only GitHub-supported qualifiers and translate common aliases.
    stripped = re.sub(
        r"\b(?:patternType|select|type|is|in):[^\s]+",
        "",
        body,
        flags=re.I,
    )
    stripped = re.sub(r"\b(?:repo|org|user):\*(?:\s|$)", "", stripped, flags=re.I)
    stripped = re.sub(r"\blang:(?=[^\s]+)", "language:", stripped, flags=re.I)
    stripped = re.sub(r"\bfile:(?=[^\s]+)", "path:", stripped, flags=re.I)
    stripped = " ".join(stripped.split())
    if stripped != body:
        rules.append("github.normalize_qualifiers")
        body = stripped

    if not _has_qualifier(body, "language") and features.languages:
        body = f"{body} language:{features.languages[0]}".strip()
        rules.append("github.language")

    if not _has_qualifier(body, "repo") and features.repo_slugs:
        slug = features.repo_slugs[0]
        # Prefer explicit bare owner/name already in text, else first feature slug
        bare = BARE_REPO_PATTERN.search(body)
        if bare:
            slug = bare.group(1)
        fold = body.casefold()
        coding_intent = features.intent == "ai_coding_and_infrastructure"
        if coding_intent or any(m in fold for m in _GH_CODE_MARKERS):
            # Avoid double-append if REPO_HINT already present via bare detection only
            if not REPO_HINT_PATTERN.search(body):
                body = f"{body} repo:{slug}".strip()
                rules.append("github.repo")

    return _result(query, body, rules)


def _augment_sourcegraph(query: str, features: QueryFeatures) -> AugmentResult:
    rules: list[str] = []
    body = clean_query(query) or features.cleaned
    if body != (query or "").strip():
        rules.append("clean.query")

    regexp_requested = re.search(r"\bpatternType:regexp\b", body, re.I) is not None
    if regexp_requested:
        body = re.sub(r"\bpatternType:regexp\b", "", body, flags=re.I)
        body = " ".join(body.split())
        rules.append("sg.pattern_type_regexp")

    if not _has_qualifier(body, "lang") and features.languages:
        sg_lang = SG_LANG_MAP.get(features.languages[0], features.languages[0].lower())
        body = f"{body} lang:{sg_lang}".strip()
        rules.append("sg.lang")

    if features.path_hints and not _has_qualifier(body, "file"):
        # Use first path hint as file: filter (escape spaces only)
        path = features.path_hints[0].replace(" ", r"\ ")
        body = f"{body} file:{path}".strip()
        rules.append("sg.file")

    words = body.split()
    # Pure NL noise reduction when many words and symbolish terms present
    op_free_words = [w for w in words if ":" not in w]
    if len(op_free_words) > 6 and features.symbolish_terms:
        focus = " ".join(features.symbolish_terms[:3])
        # Keep existing operators
        ops = [w for w in words if ":" in w]
        body = " ".join([focus, *ops]).strip()
        rules.append("sg.symbol_focus")

    return _result(
        query,
        body,
        rules,
        metadata={"pattern_type": "regexp" if regexp_requested else "literal"},
    )


def _augment_gitlab(query: str, features: QueryFeatures) -> AugmentResult:
    rules: list[str] = []
    body = clean_query(query) or features.cleaned
    if body != (query or "").strip():
        rules.append("clean.query")

    project_hints = re.findall(r"\b(?:repo|project):([^\s]+)", body, flags=re.I)
    stripped = re.sub(
        r"\b(?:repo|org|user|lang|language|path|file|filetype|patternType|select|type|is|in):[^\s]+",
        "",
        body,
        flags=re.I,
    )
    stripped = " ".join(stripped.split())
    if stripped != body:
        rules.append("gitlab.plain_text_search")
        body = stripped

    if features.languages:
        lang_word = features.languages[0]
        if lang_word.casefold() not in body.casefold():
            body = f"{body} {lang_word}".strip()
            rules.append("gitlab.language_kw")

    metadata: dict[str, str] = {}
    if project_hints:
        metadata["project_hint"] = project_hints[0]
        rules.append("gitlab.project_hint_diagnostic")
    return _result(query, body, rules, metadata=metadata)


def _strip_code_ops(body: str) -> tuple[str, bool]:
    stripped = _CODE_OPS.sub("", body)
    stripped = " ".join(stripped.split())
    return stripped, stripped != body


def _augment_hackernews(query: str, features: QueryFeatures) -> AugmentResult:
    rules: list[str] = []
    body = clean_query(query) or features.cleaned
    if body != (query or "").strip():
        rules.append("clean.query")

    body, stripped = _strip_code_ops(body)
    if stripped:
        rules.append("hn.strip_ops")

    # Prefer core + preserved terms if body became too short
    if len(body.split()) < 2 and features.preserved_terms:
        extra = " ".join(features.preserved_terms[:4])
        body = f"{body} {extra}".strip()
        rules.append("hn.preserved")

    return _result(query, body, rules)


def _augment_reddit(query: str, features: QueryFeatures) -> AugmentResult:
    rules: list[str] = []
    body = clean_query(query) or features.cleaned
    if body != (query or "").strip():
        rules.append("clean.query")

    body, stripped = _strip_code_ops(body)
    if stripped:
        rules.append("reddit.strip_ops")

    return _result(query, body, rules)


def augment_query_for_provider(
    provider: str,
    query: str,
    features: QueryFeatures,
) -> AugmentResult:
    """Dispatch dialect shaping; unknown provider → clean only."""
    name = (provider or "").strip().casefold()
    if name == "github":
        return _augment_github(query, features)
    if name == "sourcegraph":
        return _augment_sourcegraph(query, features)
    if name == "gitlab":
        return _augment_gitlab(query, features)
    if name == "hackernews":
        return _augment_hackernews(query, features)
    if name == "reddit":
        return _augment_reddit(query, features)

    cleaned = features.cleaned or clean_query(query)
    rules: list[str] = []
    if cleaned != (query or "").strip():
        rules.append("clean.query")
    return AugmentResult(
        query=cleaned or (query or ""),
        changed=cleaned != (query or ""),
        rules_applied=tuple(rules),
    )


def specialized_fallback_query(intent: str | None, features: QueryFeatures) -> str:
    """Pick a dialect-shaped specialized branch fallback query."""
    intent_s = str(intent or features.intent or "general")
    if intent_s == "ai_coding_and_infrastructure":
        return augment_query_for_provider("sourcegraph", features.cleaned, features).query
    if intent_s == "social_media":
        return augment_query_for_provider("reddit", features.cleaned, features).query
    return features.cleaned or clean_query(features.raw)


# Public alias used by planning
_specialized_fallback_query = specialized_fallback_query


def features_for_query(
    query: str,
    *,
    understanding: object | None = None,
    support_terms: tuple[str, ...] | list[str] = (),
) -> QueryFeatures:
    """Convenience wrapper for retrieval boundary."""
    return build_query_features(query, understanding=understanding, support_terms=support_terms)
