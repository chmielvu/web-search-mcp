"""Query shaping pipeline: language, segmentation, features, role rendering.

Merged from ``heuristics/{lang_detect,text_segment,query_features,shaping}.py``
with the harm fixes from the gh-evidenced overhaul:

- H1  segmentation gated to the ``free`` role; engine is wordsegment (bigram
      stupid-backoff over Norvig counts, ``LIMIT=24``) instead of wordninja.
- H2  char trims cut at word boundaries.
- H3  word trims never drop preserved terms.
- H4  phrase unquoting rebuilds via protected-range surgery; stray quotes survive.
- H5  exclude operator requires an alphabetic payload (``covid -19`` stays intact).
- H6  SERP roles keep boolean/engine operators (``AND``/``OR``/``intitle:``) that
      Brave/Google/DDG officially support.
- H7  operators parsed once in ``build_query_features``; rendering consumes
      ``features.ops``.
- H8  no redundant re-clean fallback.
- H10 Sentry-style boolean cleanup pass after span splicing (collapse
      consecutive AND/OR, strip dangling edge operators).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lingua import Language, LanguageDetectorBuilder
from wordsegment import load as _ws_load, segment as _ws_segment

from .text_clean import clean_query

if TYPE_CHECKING:
    from ..search.understanding.models import QueryUnderstandingResult

__all__ = [
    "AugmentResult",
    "QueryFeatures",
    "SearchOpClass",
    "SearchOpSpan",
    "SearchOps",
    "build_query_features",
    "detect_lang",
    "extract_search_ops",
    "langs_from_text",
    "segment_query",
    "shape_for_branch",
]

# ---------------------------------------------------------------------------
# Language identification (lingua-py, always-on)
# ---------------------------------------------------------------------------

_SUPPORTED = (
    Language.ENGLISH,
    Language.POLISH,
    Language.GERMAN,
    Language.SPANISH,
    Language.FRENCH,
)

MIN_CONFIDENCE = 0.70
MIN_MARGIN = 0.40

_detector = (
    LanguageDetectorBuilder.from_languages(*_SUPPORTED).with_preloaded_language_models().build()
)


def detect_lang(text: str) -> str:
    """Return ISO-639-1 code (lowercase) for the query language, '' if unknown.

    A detection is only trusted when the top language scores >= MIN_CONFIDENCE
    AND leads the runner-up by >= MIN_MARGIN. Short keyword-style English
    queries routinely produce confidently-wrong low-margin picks (e.g.
    'async context manager python' -> de at 0.43/0.13); those map to '' so
    callers treat them as unknown instead of acting on a guess.
    """
    if not text or not any(ch.isalpha() for ch in text):
        return ""
    values = _detector.compute_language_confidence_values(text)
    if not values:
        return ""
    top = values[0]
    second = values[1].value if len(values) > 1 else 0.0
    if top.value < MIN_CONFIDENCE or (top.value - second) < MIN_MARGIN:
        return ""
    return top.language.iso_code_639_1.name.lower()


# ---------------------------------------------------------------------------
# Segmentation v2 (wordsegment)
# ---------------------------------------------------------------------------

MIN_TOKEN_LEN = 10
MAX_TOKEN_LEN = 24  # wordsegment LIMIT: no token > 24 chars can segment

_OPERATOR_PREFIXES = (
    "repo:",
    "org:",
    "user:",
    "lang:",
    "language:",
    "path:",
    "file:",
    "site:",
    "patternType:",
)

# Step 8 fixture (2026-09-07): tokens the wordsegment corpus breaks. The other
# 31 old protected tokens survive unbroken (postgres, embedding, keyword, ...)
# and are NOT listed — fixture-proven breakages only.
_MIN_PROTECTED_TOKENS: frozenset[str] = frozenset(
    {
        "autovacuum",
        "sqlalchemy",
        "fastmcp",
        "langgraph",
        "langchain",
        "duckdb",
        "websocket",
        "websockets",
        "zstandard",
        "brotli",
        "codefetch",
        "codesearch",
        "webhook",
        "webhooks",
        "microservice",
        "microservices",
        "kubernetes",
        "dockerfile",
        "dockerfiles",
        "github",
        "gitlab",
        "bitbucket",
        "openai",
        "tensorflow",
        "pytorch",
        "opentelemetry",
        "checkpointer",
        "checkpointers",
        "rerank",
        "reranking",
        "crossencoder",
        "biencoder",
        "vectorstore",
        "vectorstores",
        "tokenizers",
        "autosuggest",
        "researchgoal",
        "queryplan",
        "queryplanner",
        "frontend",
    }
)

_ws_load()


def _is_eligible_token(token: str) -> bool:
    """True when a whitespace token may be run through wordsegment.

    Eligible: pure alphabetic, all-lowercase, between MIN_TOKEN_LEN and
    MAX_TOKEN_LEN chars, no underscores/dots, not an operator token. Quotes
    are tolerated around the core but quotes alone never make a token eligible.
    """
    core = token.strip("\"'")
    if len(core) < MIN_TOKEN_LEN or len(core) > MAX_TOKEN_LEN:
        return False
    if not core.isalpha() or core != core.lower():
        return False
    if "_" in core or "." in core:
        return False
    if core.casefold() in _MIN_PROTECTED_TOKENS:
        return False
    lower = token.lower()
    if lower.startswith(_OPERATOR_PREFIXES):
        return False
    return True


# ---------------------------------------------------------------------------
# Language token helpers (from query_features.py; public per cutover map)
# ---------------------------------------------------------------------------

_LANG_TOKEN_PATTERN = re.compile(r"\b(?:lang|language):([A-Za-z0-9+#.]+)\b", re.I)

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


def _uniq(items) -> tuple[str, ...]:  # type: ignore[no-untyped-def]
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


def langs_from_text(text: str, domain_hints=()) -> tuple[str, ...]:  # type: ignore[no-untyped-def]
    """Extract canonical language labels from ``lang:`` tokens and bare words."""
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


def segment_query(text: str) -> str | None:
    """Split eligible glued tokens in-place; return None when nothing changed.

    wordsegment's bigram corpus decides the split; short function words
    (``in``, ``new``) are legitimate segments. No all-parts>=3 guard — that
    rule rejected valid splits like ``toplawyersinnewyork``.
    """
    if not text:
        return None
    out: list[str] = []
    changed = False
    for token in text.split():
        if _is_eligible_token(token):
            parts = list(_ws_segment(token.strip("\"'")))
            joined = " ".join(parts)
            if len(parts) > 1 and joined != token.strip("\"'"):
                out.append(joined)
                changed = True
                continue
        out.append(token)
    if not changed:
        return None
    return " ".join(out)


# Words dropped first when a budget trim must shed tokens (from _STOP; only
# consumed by trims — the old symbol/regexp tables are gone).
_TRIM_STOPWORDS = frozenset(
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

# ---------------------------------------------------------------------------
# Operator model (moved from shaping.py; dead properties removed)
# ---------------------------------------------------------------------------


class SearchOpClass:
    PHRASE = "phrase"
    SITE = "site"
    FILETYPE = "filetype"
    EXCLUDE = "exclude"
    ENGINE = "engine"


_PHRASE_RE = re.compile(r'"([^"\n]{1,120})"')
_SITE_RE = re.compile(r'\bsite:([^\s"]+)', re.I)
_FILETYPE_RE = re.compile(r'\b(?:filetype|ext):([^\s"]+)', re.I)
# H5: payload must start with a letter — "covid -19 vaccine" is not an exclude.
_EXCLUDE_RE = re.compile(r'(?:^|\s)-((?:site:|filetype:|ext:)?[a-z][^\s"]{0,40})', re.I)
_ENGINE_KV_RE = re.compile(
    r'\b(?:intitle|inbody|inpage|inurl|lang|language|loc|location|link|related|cache):([^\s"]+)',
    re.I,
)
_PLUS_RE = re.compile(r'(?:^|\s)\+([a-z0-9][^\s"]{0,30})', re.I)
_BOOL_RE = re.compile(r"\b(?:AND|OR|NOT)\b")

_SITE_VALUE_RE = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", re.I)
_EXT_VALUE_RE = re.compile(r"[a-z0-9]{1,10}", re.I)

_MAX_SPANS = 16
_MAX_PRESERVED_SCANS = 24


@dataclass(frozen=True, slots=True)
class SearchOpSpan:
    op_class: str
    start: int
    end: int  # exclusive, on the original surface
    text: str  # exact matched surface (prefix and quotes included)
    value: str  # validated payload (phrase content without quotes)


@dataclass(frozen=True, slots=True)
class SearchOps:
    spans: tuple[SearchOpSpan, ...] = ()
    truncated: bool = False

    def of_class(self, op_class: str) -> tuple[SearchOpSpan, ...]:
        return tuple(span for span in self.spans if span.op_class == op_class)


def _span(op_class: str, match: re.Match[str], value_group: int | None) -> SearchOpSpan | None:
    value = match.group(value_group) if value_group is not None else match.group(0)
    if value is None:
        return None
    return SearchOpSpan(
        op_class=op_class,
        start=match.start(),
        end=match.end(),
        text=match.group(0),
        value=value.strip(),
    )


def _valid_payload(span: SearchOpSpan) -> bool:
    """Stage 2 — deterministic structural validation."""
    # Offsets must reproduce the source surface exactly.
    if span.end <= span.start or not span.text:
        return False
    if span.op_class == SearchOpClass.SITE:
        return _SITE_VALUE_RE.fullmatch(span.value) is not None
    if span.op_class == SearchOpClass.FILETYPE:
        return _EXT_VALUE_RE.fullmatch(span.value) is not None
    if span.op_class == SearchOpClass.EXCLUDE:
        core = span.value.split(":", 1)[-1]
        return bool(core) and len(core) <= 41
    if span.op_class == SearchOpClass.ENGINE:
        return len(span.value) <= 64
    return True


def extract_search_ops(query: str) -> SearchOps:
    """Stage 1+2: bounded candidate scan with validation.

    Candidates are collected from the ORIGINAL string; ``text[start:end]``
    reproduces the matched surface by construction.
    """
    if not query or not query.strip():
        return SearchOps()
    found: list[SearchOpSpan] = []
    for match in _PHRASE_RE.finditer(query):
        span = _span(SearchOpClass.PHRASE, match, 1)
        if span:
            found.append(span)
    for pattern, op_class, group in (
        (_SITE_RE, SearchOpClass.SITE, 1),
        (_FILETYPE_RE, SearchOpClass.FILETYPE, 1),
        (_EXCLUDE_RE, SearchOpClass.EXCLUDE, 1),
        (_ENGINE_KV_RE, SearchOpClass.ENGINE, 0),
        (_PLUS_RE, SearchOpClass.ENGINE, 1),
        (_BOOL_RE, SearchOpClass.ENGINE, None),
    ):
        for match in pattern.finditer(query):
            span = _span(op_class, match, group)
            if span:
                found.append(span)
    valid = [span for span in found if _valid_payload(span)]
    valid.sort(key=lambda s: (s.start, -s.end, s.op_class))

    # Stage 3a — containment suppression: an exclude carrying a structured
    # prefix (-site:x / -filetype:y) swallows the inner bare-key candidate.
    kept: list[SearchOpSpan] = []
    for span in valid:
        contained = any(
            other.start <= span.start and span.end <= other.end and other is not span
            for other in valid
            if other.op_class == SearchOpClass.EXCLUDE
            and span.op_class in (SearchOpClass.SITE, SearchOpClass.FILETYPE)
        )
        if not contained:
            kept.append(span)
    truncated = len(kept) > _MAX_SPANS
    return SearchOps(spans=tuple(kept[:_MAX_SPANS]), truncated=truncated)


# ---------------------------------------------------------------------------
# Role spec table (replaces _Dialects; H6 boolean/engine keep for SERP)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _RoleSpec:
    kept_ops: frozenset[str]
    phrases_quoted: bool
    max_words: int | None = None
    max_chars: int | None = None
    segment: bool = False


_SERP_OPS = frozenset({"phrase", "site", "filetype", "exclude", "engine"})
_ROLE_SPECS: dict[str, _RoleSpec] = {
    "original": _RoleSpec(_SERP_OPS, True),
    "serp1": _RoleSpec(_SERP_OPS, True, max_words=50, max_chars=400),
    "serp2": _RoleSpec(_SERP_OPS, True),
    "free": _RoleSpec(frozenset({"phrase"}), False, max_words=12, segment=True),
    "semantic_tavily": _RoleSpec(frozenset({"phrase"}), False),
    "semantic_exa": _RoleSpec(frozenset({"phrase"}), False),
}
_DEFAULT_SPEC = _RoleSpec(_SERP_OPS, True)


@dataclass(frozen=True, slots=True)
class AugmentResult:
    query: str
    changed: bool
    rules_applied: tuple[str, ...]
    metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class QueryFeatures:
    """Parsed-once query features shared by planning and rendering."""

    raw: str
    cleaned: str
    ops: SearchOps
    lang: str
    preserved_terms: tuple[str, ...]
    compared_entities: tuple[str, ...] = ()
    time_sensitivity: str = "none"
    segmented_variants: tuple[str, ...] = ()


def build_query_features(
    query: str,
    *,
    understanding: "QueryUnderstandingResult | Any | None" = None,
    support_terms=(),  # type: ignore[no-untyped-def]
) -> QueryFeatures:
    """Parse the query once: clean, ops, lang, preserved terms, understanding."""
    raw = query or ""
    cleaned = clean_query(raw)
    preserved: list[str] = []
    if understanding is not None:
        preserved.extend(getattr(understanding, "preserved_terms", None) or [])
    preserved.extend(support_terms or ())
    compared_entities = _uniq(getattr(understanding, "compared_entities", None) or [])
    ts_raw = getattr(understanding, "time_sensitivity", None)
    time_sensitivity = str(ts_raw) if ts_raw else "none"
    lang = detect_lang(cleaned)
    # Segment English and ambiguous queries only when a role asks for it; the
    # calibrated confidence+margin gate in detect_lang means '' reliably means
    # "too short or ambiguous to trust" (true non-English queries score
    # high-margin), so attempting segmentation there is safe. wordsegment
    # leaves real words unchanged; only genuinely glued tokens produce a variant.
    segmented_variants: tuple[str, ...] = ()
    if lang in ("en", ""):
        segmented = segment_query(cleaned)
        if segmented:
            segmented_variants = (segmented,)
    return QueryFeatures(
        raw=raw,
        cleaned=cleaned,
        ops=extract_search_ops(cleaned),
        lang=lang,
        preserved_terms=_uniq(preserved),
        compared_entities=compared_entities,
        time_sensitivity=time_sensitivity,
        segmented_variants=segmented_variants,
    )


# ---------------------------------------------------------------------------
# Rendering (shape_for_branch; fixes H2/H3/H4/H10)
# ---------------------------------------------------------------------------


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


def _preserved_ranges(query: str, preserved_terms: tuple[str, ...]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    scans = 0
    for term in preserved_terms[:12]:
        if not term or len(term) < 2:
            continue
        for match in re.finditer(re.escape(term), query, re.I):
            ranges.append((match.start(), match.end()))
            scans += 1
            if scans >= _MAX_PRESERVED_SCANS:
                return ranges
    return ranges


def _inside_any(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(rs <= start and end <= rend for rs, rend in ranges)


# Sentry cleanup_search_query precedence: between two boolean tokens whose
# filter span was removed, OR wins over AND; identical collapse to the weaker.
_BOOL_COLLAPSE = {
    ("AND", "AND"): "AND",
    ("AND", "OR"): "OR",
    ("OR", "AND"): "OR",
    ("OR", "OR"): "OR",
}
_BOOL_TOKEN_RE = re.compile(r"\b(?:AND|OR|NOT)\b")


def _cleanup_boolean_ops(body: str) -> str:
    """Sentry-style post-splice boolean safety pass.

    Collapses consecutive boolean tokens (OR wins over AND) and strips
    leading/trailing AND/OR/NOT left dangling after filter-span removal.
    """
    tokens = list(_BOOL_TOKEN_RE.finditer(body))
    if not tokens:
        return body
    # Collapse consecutive operators.
    out: list[str] = []
    cursor = 0
    index = 0
    while index < len(tokens):
        first = tokens[index]
        out.append(body[cursor : first.start()])
        group = [first]
        while (
            index + 1 < len(tokens)
            and not body[tokens[index].end() : tokens[index + 1].start()].strip()
        ):
            index += 1
            group.append(tokens[index])
        if len(group) == 1:
            out.append(first.group(0))
        else:
            values = [g.group(0) for g in group]
            collapsed = values[0]
            for nxt in values[1:]:
                collapsed = _BOOL_COLLAPSE.get((collapsed, nxt), "AND" if collapsed == nxt else nxt)
                if collapsed not in ("AND", "OR"):
                    collapsed = "OR"
            out.append(collapsed)
        cursor = group[-1].end()
        index += 1
    out.append(body[cursor:])
    cleaned = "".join(out)
    # Strip dangling edge operators.
    cleaned = re.sub(r"^(?:\s*(?:AND|OR|NOT)\s+)+", "", cleaned)
    cleaned = re.sub(r"(?:\s+(?:AND|OR|NOT)\s*)+$", "", cleaned)
    return cleaned


def _trim_words(body: str, max_words: int, protected: list[tuple[int, int]]) -> str:
    """Word-boundary trim that sheds stopwords first, then the tail; never a
    preserved term."""
    words = body.split()
    if len(words) <= max_words:
        return body
    # Build (start, end) spans per word on the joined body.
    spans: list[tuple[int, int]] = []
    cursor = 0
    for word in words:
        start = body.index(word, cursor)
        spans.append((start, start + len(word)))
        cursor = start + len(word)
    droppable = [
        i for i, word in enumerate(words) if not _inside_any(spans[i][0], spans[i][1], protected)
    ]
    keep = set(range(len(words)))
    stop_order = [i for i in droppable if word_key(words[i]) in _TRIM_STOPWORDS]
    tail_order = [i for i in sorted(droppable, reverse=True)]
    for pool in (stop_order, tail_order):
        for i in pool:
            if len(keep) <= max_words:
                break
            keep.discard(i)
    return " ".join(words[i] for i in sorted(keep))


def word_key(word: str) -> str:
    return word.casefold().strip("\"'.,!?;:")


def _trim_chars(body: str, max_chars: int, protected: list[tuple[int, int]]) -> str:
    """Char trim at the last word boundary <= max_chars; never cuts mid-word."""
    if len(body) <= max_chars:
        return body
    cut = body.rfind(" ", 0, max_chars + 1)
    if cut <= 0:
        # Single long token: hard cut only when unprotected.
        if not _inside_any(0, max_chars, protected):
            return body[:max_chars]
        cut = max_chars
    candidate = body[:cut].rstrip()
    if not candidate:
        candidate = body[:max_chars]
    return candidate


def shape_for_branch(
    role: str, query: str, features: QueryFeatures, *, exact: bool = False
) -> AugmentResult:
    """Shape one branch query for its role dialect. Pure function.

    ``exact=True`` returns the query verbatim (no operator surgery, no
    budget trims, no unquoting, no glue segmentation).
    """
    spec = _ROLE_SPECS.get(str(role), _DEFAULT_SPEC)
    original = query or ""
    meta: dict[str, str] = {"role": str(role)}
    rules: list[str] = []

    if exact:
        return _result(original, original or "", ["exact"], meta)

    # Lang gate first — non-English queries never receive operator surgery.
    if features.lang not in ("", "en"):
        body = features.cleaned or clean_query(original) or original
        return _result(original, body, ["skip.non_english"], meta)

    ops = features.ops  # parsed once (H7)
    stripped_classes = sorted(
        cls for cls in ("site", "filetype", "exclude", "engine") if cls not in spec.kept_ops
    )
    to_strip = [span for cls in stripped_classes for span in ops.of_class(cls)]
    kept_phrases = ops.of_class(SearchOpClass.PHRASE)

    # Guard: never strip inside a kept phrase or a GLiNER-preserved term.
    protected = [(p.start, p.end) for p in kept_phrases]
    protected += _preserved_ranges(original, features.preserved_terms)
    to_strip = [s for s in to_strip if not _inside_any(s.start, s.end, protected)]

    body = original
    if to_strip:
        for span in sorted(to_strip, key=lambda s: -s.start):
            body = body[: span.start] + body[span.end :]
        body = _cleanup_boolean_ops(body)  # H10: Sentry safety pass
        rules.append("strip.ops")
        meta["ops.stripped"] = ",".join(f"{s.op_class}:{s.value}" for s in to_strip[:8])
    elif ops.truncated:
        meta["ops.overflow"] = "truncated"

    if spec.phrases_quoted is False and kept_phrases:
        # H4: unquote ONLY parsed phrase spans via protected-range surgery.
        for phrase_span in sorted(kept_phrases, key=lambda s: -s.start):
            if _inside_any(
                phrase_span.start,
                phrase_span.end,
                _preserved_ranges(body, features.preserved_terms),
            ):
                continue
            start = phrase_span.start - 1
            end = phrase_span.end + 1
            if 0 <= start < end <= len(body) and body[start:end] == f'"{phrase_span.value}"':
                body = body[:start] + phrase_span.value + body[end:]
            else:
                # Surface drifted (post-strip offsets); fall back to exact
                # surface replacement, never a global quote nuke.
                surface = f'"{phrase_span.value}"'
                idx = body.find(surface)
                if idx >= 0:
                    body = body[:idx] + phrase_span.value + body[idx + len(surface) :]
        rules.append("unquote.phrases")
    if spec.max_words is not None:
        words = body.split()
        if len(words) > spec.max_words:
            meta["budget.trim.words"] = f"{len(words)}->{spec.max_words}"
            body = _trim_words(body, spec.max_words, protected)  # H3
            rules.append("budget.trim")
    if spec.max_chars is not None and len(body) > spec.max_chars:
        meta["budget.trim.chars"] = f"{len(body)}->{spec.max_chars}"
        body = _trim_chars(body, spec.max_chars, protected)  # H2
        rules.append("budget.trim")

    # Additive glue-repair ONLY for roles that ask for it (H1: free).
    if spec.segment:
        segmented = segment_query(body)
        if segmented:
            body = segmented
            rules.append("segment.glued")

    return _result(original, body, rules, meta)
