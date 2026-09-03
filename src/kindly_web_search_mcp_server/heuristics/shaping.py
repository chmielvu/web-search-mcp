"""Role-dialect query shaping for branch retrieval (pure, no network).

Cascade (all stages explicit):
  1. candidates  ``extract_search_ops`` — bounded linear scan over the ORIGINAL
                 surface so offsets are always reproducible.
  2. validate    per-class structural checks (``re.fullmatch`` payloads).
  3. resolve     longest span wins; containment suppression for paired classes;
                 deterministic tiebreak ``(start, -end, op_class)``.
  4. normalize   parallel views only — the original surface is spliced, never
                 rewritten; wordninja glue-repair is additive (``segment.glued``).
  5. explain     ``AugmentResult.rules_applied`` + sorted metadata tuples.

Dialect is keyed on BRANCH ROLE (never provider name). The lang gate
(``skip.non_english``) applies to every role before any operator work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .query_features import QueryFeatures
from .text_clean import clean_query
from .text_segment import segment_query

__all__ = [
    "AugmentResult",
    "SearchOpClass",
    "SearchOpSpan",
    "SearchOps",
    "extract_search_ops",
    "shape_for_branch",
]


class SearchOpClass:
    PHRASE = "phrase"
    SITE = "site"
    FILETYPE = "filetype"
    EXCLUDE = "exclude"
    ENGINE = "engine"


# --- Stage 1/2: candidate patterns + payload validators (python-re, no lookarounds) ---

_PHRASE_RE = re.compile(r'"([^"\n]{1,120})"')
_SITE_RE = re.compile(r'\bsite:([^\s"]+)', re.I)
_FILETYPE_RE = re.compile(r'\b(?:filetype|ext):([^\s"]+)', re.I)
_EXCLUDE_RE = re.compile(r'(?:^|\s)-((?:site:|filetype:|ext:)?[a-z0-9][^\s"]{0,40})', re.I)
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

    @property
    def phrases(self) -> tuple[SearchOpSpan, ...]:
        return self.of_class(SearchOpClass.PHRASE)

    @property
    def sites(self) -> tuple[SearchOpSpan, ...]:
        return self.of_class(SearchOpClass.SITE)

    @property
    def filetypes(self) -> tuple[SearchOpSpan, ...]:
        return self.of_class(SearchOpClass.FILETYPE)

    @property
    def excludes(self) -> tuple[SearchOpSpan, ...]:
        return self.of_class(SearchOpClass.EXCLUDE)

    @property
    def engine_only(self) -> tuple[SearchOpSpan, ...]:
        return self.of_class(SearchOpClass.ENGINE)


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


# --- Role dialects (compiled table; role values mirror BranchRole) ---

_ALLOWED_ALL = frozenset(
    {
        SearchOpClass.PHRASE,
        SearchOpClass.SITE,
        SearchOpClass.FILETYPE,
        SearchOpClass.EXCLUDE,
        SearchOpClass.ENGINE,
    }
)
_LCD = frozenset(
    {
        SearchOpClass.PHRASE,
        SearchOpClass.SITE,
        SearchOpClass.FILETYPE,
        SearchOpClass.EXCLUDE,
    }
)


@dataclass(frozen=True, slots=True)
class _Dialect:
    allowed: frozenset[str] = _ALLOWED_ALL
    unquote_phrases: bool = False
    max_words: int | None = None
    max_chars: int | None = None


_DIALECTS: dict[str, _Dialect] = {
    "original": _Dialect(),
    "free": _Dialect(allowed=frozenset({SearchOpClass.PHRASE}), max_words=12),
    "serp1": _Dialect(allowed=_LCD, max_words=50, max_chars=400),
    "serp2": _Dialect(allowed=_LCD),
    "semantic_tavily": _Dialect(allowed=frozenset(), unquote_phrases=True),
    "semantic_exa": _Dialect(allowed=frozenset(), unquote_phrases=True),
}
_DEFAULT_DIALECT = _Dialect()


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


def shape_for_branch(
    role: str, query: str, features: QueryFeatures, *, exact: bool = False
) -> AugmentResult:
    """Shape one branch query for its role dialect. Pure function.

    ``exact=True`` returns the query verbatim (no operator surgery, no
    budget trims, no unquoting, no glue segmentation).
    """
    dialect = _DIALECTS.get(str(role), _DEFAULT_DIALECT)
    original = query or ""
    meta: dict[str, str] = {"role": str(role)}
    rules: list[str] = []

    if exact:
        return _result(original, original or "", ["exact"], meta)

    # Lang gate first — non-English queries never receive operator surgery.
    if features.lang not in ("", "en"):
        body = features.cleaned or clean_query(original) or original
        return _result(original, body, ["skip.non_english"], meta)

    ops = extract_search_ops(original)
    stripped_classes = sorted(
        cls for cls in ("site", "filetype", "exclude", "engine") if cls not in dialect.allowed
    )
    to_strip = [span for cls in stripped_classes for span in ops.of_class(cls)]
    kept_phrases = ops.phrases

    # Guard: never strip inside a kept phrase or a GLiNER-preserved term.
    protected = [(p.start, p.end) for p in kept_phrases]
    protected += _preserved_ranges(original, features.preserved_terms)
    to_strip = [s for s in to_strip if not _inside_any(s.start, s.end, protected)]

    body = original
    if to_strip:
        for span in sorted(to_strip, key=lambda s: -s.start):
            body = body[: span.start] + body[span.end :]
        rules.append("strip.ops")
        meta["ops.stripped"] = ",".join(f"{s.op_class}:{s.value}" for s in to_strip[:8])
    elif ops.truncated:
        meta["ops.overflow"] = "truncated"

    if dialect.unquote_phrases and kept_phrases:
        body = body.replace('"', "")
        rules.append("unquote.phrases")
    if dialect.max_words is not None:
        words = body.split()
        if len(words) > dialect.max_words:
            meta["budget.trim.words"] = f"{len(words)}->{dialect.max_words}"
            body = " ".join(words[: dialect.max_words])
            rules.append("budget.trim")
    if dialect.max_chars is not None and len(body) > dialect.max_chars:
        meta["budget.trim.chars"] = f"{len(body)}->{dialect.max_chars}"
        body = body[:dialect.max_chars]
        rules.append("budget.trim")

    # Additive wordninja glue-repair on the final shaped form.
    segmented = segment_query(body)
    if segmented:
        body = segmented
        rules.append("segment.glued")
    else:
        cleaned = clean_query(body)
        if cleaned and cleaned != body:
            body = cleaned
            rules.append("clean.query")

    return _result(original, body, rules, meta)
