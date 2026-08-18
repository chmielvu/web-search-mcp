"""Deterministic query parsing and provider-specific variant planning."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .models import QueryMetadata

_QUERY_MAX_CHARS = 256
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "can",
    "does",
    "how",
    "i",
    "implement",
    "implemented",
    "implementation",
    "implementations",
    "find",
    "show",
    "where",
    "in",
    "is",
    "of",
    "on",
    "people",
    "the",
    "to",
    "use",
    "what",
    "with",
}
_DISCOVERY_WORDS = {
    "implementation",
    "implementations",
    "library",
    "libraries",
    "project",
    "projects",
    "repo",
    "repos",
    "repository",
    "repositories",
}

_STRUCTURAL_WORDS = {
    "class",
    "def",
    "definition",
    "enum",
    "function",
    "interface",
    "method",
    "struct",
    "trait",
}
_QUALIFIED_IDENTIFIER = re.compile(
    r"(?<![\w.])(?P<value>[A-Za-z_][A-Za-z0-9_]*(?:(?:::|\.|#)[A-Za-z_][A-Za-z0-9_]*)+)"
)
_SOURCE_IDENTIFIER = re.compile(
    r"(?<![\w.])(?P<value>__[A-Za-z0-9_]+__|[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+|"
    r"[a-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*|[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+)(?![\w.])"
)
_QUALIFIER_KEYS = {
    "archived",
    "created",
    "extension",
    "file",
    "filename",
    "followers",
    "fork",
    "forks",
    "good-first-issues",
    "help-wanted-issues",
    "in",
    "is",
    "lang",
    "language",
    "license",
    "mirror",
    "org",
    "path",
    "pushed",
    "repo",
    "rev",
    "revision",
    "size",
    "sponsorships",
    "stars",
    "sym",
    "symbol",
    "template",
    "topic",
    "topics",
    "user",
    "visibility",
    "-content",
    "-extension",
    "-file",
    "-filename",
    "-lang",
    "-language",
    "-org",
    "-path",
    "-repo",
    "-user",
}
_REGEX_TOKEN = re.compile(r"(?:^|\s)(/(?:[^/\\\r\n]|\\.)+/(?:[imsu]*))(?=$|\s)")
_QUALIFIER_TOKEN = re.compile(r"^(?P<key>-?[A-Za-z][A-Za-z0-9_-]*):(?P<value>.+)$")
_FILE_PATH_TAIL = re.compile(r"(?:^|[\\/])(?P<filename>[^\\/]+\.[A-Za-z0-9_-]{1,10})$")
_BOOLEAN_TOKENS = {"AND", "OR", "NOT"}


def _derive_path_and_filename(
    path: str | None, filename: str | None
) -> tuple[str | None, str | None]:
    """Extract filename and directory path when path points to a specific file tail."""
    if not path or filename:
        return path, filename
    clean_path = path.strip().strip('"').strip("'")
    match = _FILE_PATH_TAIL.search(clean_path)
    if match:
        derived_filename = match.group("filename")
        dir_part = clean_path[: match.start()].rstrip("/\\")
        derived_path = dir_part if dir_part else None
        return derived_path, derived_filename
    return path, filename


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """Internal plan retaining compiled regex state separately from display text."""

    original_query: str
    search_text: str
    api_query: str
    variants: tuple[str, ...]
    regex_source: str | None
    local_regex: re.Pattern[str] | None
    anchor_terms: tuple[str, ...]
    qualifiers: tuple[tuple[str, str], ...]
    warnings: tuple[str, ...]
    variant_kinds: tuple[str, ...] = ()
    source_tokens: tuple[tuple[str, str, str, str], ...] = ()
    concept_terms: tuple[str, ...] = ()
    structural_kind: str | None = None
    exa_semantic_query: str = ""
    mode: str = "code"

    @property
    def metadata(self) -> QueryMetadata:
        channels = {"lexical"}
        channels.update(self.variant_kinds)
        if self.regex_source:
            channels.add("regex")
        if self.mode == "docs":
            channels.add("documentation")
        if self.mode == "discovery":
            channels.add("repository")
        return QueryMetadata(
            original_query=self.original_query,
            variants=list(self.variants),
            regex_source=self.regex_source,
            anchor_terms=list(self.anchor_terms),
            qualifiers=dict(self.qualifiers),
            warnings=list(self.warnings),
            variant_kinds=list(self.variant_kinds),
            source_tokens=[
                {"value": value, "leaf": leaf, "parent": parent, "shape": shape}
                for value, leaf, parent, shape in self.source_tokens
            ],
            concept_terms=list(self.concept_terms),
            structural_kind=self.structural_kind,
            mode=self.mode,
            backend_channels=sorted(channels),
        )

    @property
    def variant_pairs(self) -> tuple[tuple[str, str], ...]:
        return tuple(zip(self.variants, self.variant_kinds, strict=False))

    def provider_query(self, variant: str, *, include_qualifiers: bool = True) -> str:
        """Return a bounded query string without leaking the local regex token."""

        value = variant.strip()
        if include_qualifiers and self.qualifiers:
            qualifier_text = " ".join(f"{key}:{item}" for key, item in self.qualifiers)
            value = f"{value} {qualifier_text}" if value else qualifier_text
        return value[:_QUERY_MAX_CHARS].rstrip()

    def grep_expression(self) -> str:
        """Return the expression used by regex-capable providers."""

        return self.regex_source or self.search_text or self.api_query


def _extract_regex_token(query: str) -> tuple[str | None, re.Pattern[str] | None, str, list[str]]:
    warnings: list[str] = []
    match = _REGEX_TOKEN.search(query)
    if not match:
        return None, None, query, warnings

    token = match.group(1)
    pattern, flags_text = token[1:].rsplit("/", 1)
    flags = 0
    if "i" in flags_text:
        flags |= re.IGNORECASE
    if "m" in flags_text:
        flags |= re.MULTILINE
    if "s" in flags_text:
        flags |= re.DOTALL
    try:
        compiled = re.compile(pattern.replace(r"\/", "/"), flags)
    except re.error as exc:
        warnings.append(f"Malformed regex token ignored: {exc.msg}")
        return None, None, query, warnings
    remaining = f"{query[: match.start()]} {query[match.end() :]}".strip()
    return pattern, compiled, remaining, warnings


def _compile_explicit_regex(
    query: str, warnings: list[str]
) -> tuple[str | None, re.Pattern[str] | None]:
    try:
        return query, re.compile(query)
    except re.error as exc:
        warnings.append(f"Malformed regexp query ignored: {exc.msg}")
        return None, None


def _split_tokens(query: str) -> list[str]:
    """Split search syntax while retaining quotes, escapes, and grouping."""

    tokens: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False

    def flush() -> None:
        if current:
            tokens.append("".join(current))
            current.clear()

    for char in query:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\" and quote:
            current.append(char)
            escaped = True
            continue
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            current.append(char)
        elif char.isspace():
            flush()
        elif char in "()":
            flush()
            tokens.append(char)
        else:
            current.append(char)
    flush()
    return tokens


def _split_qualifiers(query: str) -> tuple[list[tuple[str, str]], list[str]]:
    qualifiers: list[tuple[str, str]] = []
    terms: list[str] = []
    for token in _split_tokens(query):
        match = _QUALIFIER_TOKEN.match(token)
        if match and match.group("key").casefold() in _QUALIFIER_KEYS:
            qualifiers.append((match.group("key").casefold(), match.group("value")))
        else:
            terms.append(token)
    return qualifiers, terms


def _strip_stopwords(terms: Iterable[str]) -> list[str]:
    cleaned: list[str] = []
    for term in terms:
        if term in _BOOLEAN_TOKENS or term in {"(", ")"}:
            cleaned.append(term)
            continue
        value = re.sub(r"^[^A-Za-z0-9_]+|[^A-Za-z0-9_]+$", "", term.strip("\"'"))
        if not value:
            continue
        if value.casefold() in _STOPWORDS:
            continue
        cleaned.append(term)
    return cleaned


def _source_shaped_tokens(query: str) -> list[tuple[str, str, str, str]]:
    values: dict[str, tuple[str, str, str, str]] = {}
    for shape, pattern in (
        ("qualified", _QUALIFIED_IDENTIFIER),
        ("identifier", _SOURCE_IDENTIFIER),
    ):
        for match in pattern.finditer(query):
            value = match.group("value").rstrip("(")
            if (
                "." in value
                and value.rsplit(".", 1)[-1].casefold()
                in {
                    "c",
                    "cc",
                    "cpp",
                    "cs",
                    "go",
                    "java",
                    "js",
                    "jsx",
                    "py",
                    "rb",
                    "rs",
                    "ts",
                    "tsx",
                }
                and "::" not in value
                and "#" not in value
            ):
                continue
            separator = "::" if "::" in value else ("#" if "#" in value else ".")
            parts = value.split(separator)
            values.setdefault(
                value.casefold(), (value, parts[-1], separator.join(parts[:-1]), shape)
            )
            if len(values) >= 6:
                return list(values.values())
    return list(values.values())


def _split_identifier(value: str) -> list[str]:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    value = re.sub(r"[^A-Za-z0-9]+", " ", value)
    return [part for part in value.split() if len(part) >= 3]


def _identifier_permutations(terms: Iterable[str]) -> list[str]:
    words = [part for term in terms for part in _split_identifier(term)]
    words = list(dict.fromkeys(word for word in words if word.casefold() not in _STOPWORDS))
    if not 2 <= len(words) <= 4:
        return []
    return list(
        dict.fromkeys(
            (
                "_".join(word.casefold() for word in words),
                words[0].casefold() + "".join(word[:1].upper() + word[1:] for word in words[1:]),
                "".join(word[:1].upper() + word[1:] for word in words),
            )
        )
    )


def _query_signals(
    query: str,
    terms: list[str],
    source_tokens: list[tuple[str, str, str, str]],
    regex_source: str | None,
) -> tuple[list[str], str | None]:
    words = {item.casefold() for item in re.findall(r"[A-Za-z][A-Za-z0-9_-]*", query)}
    concepts = [
        cleaned
        for item in terms
        if (cleaned := re.sub(r"^[^A-Za-z0-9_]+|[^A-Za-z0-9_]+$", "", item.strip("\"'")))
        and cleaned.casefold() not in _STRUCTURAL_WORDS | _DISCOVERY_WORDS
    ]
    structural_kind = next((word for word in _STRUCTURAL_WORDS if word in words), None)
    return (concepts, structural_kind)


def _literal_anchors(text: str) -> list[str]:
    """Extract distinctive literal terms from a regex or free-text query."""

    literals = re.sub(r"\\(.)", r"\1", text)
    literals = re.sub(r"[\[\]().*+?{}^$|]", " ", literals)
    terms = [term.strip("\"'") for term in re.split(r"\s+", literals) if term.strip("\"'")]
    unique: dict[str, str] = {}
    for term in terms:
        if len(term) < 3 or term.casefold() in _STOPWORDS:
            continue
        unique.setdefault(term.casefold(), term)
    return sorted(unique.values(), key=lambda item: (-len(item), item.casefold()))


def _split_top_level_alternatives(pattern: str) -> list[str]:
    """Split only top-level regex alternatives, preserving grouped expressions."""

    alternatives: list[str] = []
    start = 0
    depth = 0
    escaped = False
    in_class = False
    for index, char in enumerate(pattern):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "[" and not in_class:
            in_class = True
            continue
        if char == "]" and in_class:
            in_class = False
            continue
        if in_class:
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "|" and depth == 0:
            alternatives.append(pattern[start:index])
            start = index + 1
    alternatives.append(pattern[start:])
    return [item.strip() for item in alternatives if item.strip()]


def _merge_qualifiers(
    parsed: list[tuple[str, str]],
    explicit: Iterable[tuple[str, str | None]],
    warnings: list[str],
) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for key, value in [*parsed, *[(k, v) for k, v in explicit if v]]:
        if key not in _QUALIFIER_KEYS:
            warnings.append(f"Unsupported qualifier ignored: {key}")
            continue
        normalized = (key.casefold(), str(value).strip())
        if normalized[1] and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return tuple(result)


def _bounded_query(value: str) -> str:
    if len(value) <= _QUERY_MAX_CHARS:
        return value.strip()
    return value[:_QUERY_MAX_CHARS].rsplit(" ", 1)[0].strip()


def build_query_plan(
    query: str,
    *,
    regexp: bool = False,
    deep: bool = False,
    repositories: Iterable[str] = (),
    language: str | None = None,
    path: str | None = None,
    filename: str | None = None,
    extension: str | None = None,
    max_variants: int = 3,
    mode: str = "code",
) -> QueryPlan:
    """Compile a query without model calls or provider-specific mutation."""

    original = query.strip()
    warnings: list[str] = []
    for scope in repositories:
        normalized_scope = scope.strip().removeprefix("https://github.com/").strip("/")
        if normalized_scope and "/" not in normalized_scope:
            warnings.append(f"Repository scope requires owner/repo: {scope!r}")
    regex_source, local_regex, remaining, regex_warnings = _extract_regex_token(original)
    warnings.extend(regex_warnings)
    if regexp and local_regex is None and remaining:
        regex_source, local_regex = _compile_explicit_regex(remaining, warnings)

    parsed_qualifiers, raw_terms = _split_qualifiers(remaining)
    boolean_count = sum(term in _BOOLEAN_TOKENS for term in raw_terms)
    if boolean_count > 5:
        warnings.append("GitHub legacy search accepts at most five Boolean operators.")
    eff_path, eff_filename = _derive_path_and_filename(path, filename)
    explicit_qualifiers: list[tuple[str, str | None]] = []
    explicit_qualifiers.extend(
        [
            ("language", language),
            ("path", eff_path),
            ("filename", eff_filename),
            ("extension", extension),
        ]
    )
    qualifiers = _merge_qualifiers(parsed_qualifiers, explicit_qualifiers, warnings)

    terms = _strip_stopwords(raw_terms)
    if not terms and raw_terms:
        terms = raw_terms[:3]
    if not terms and regex_source:
        terms = _literal_anchors(regex_source)[:3] or ["regex"]
    if not terms and not qualifiers:
        warnings.append("Query has no searchable terms or qualifiers.")

    content_terms = [
        term
        for term in terms
        if re.sub(r"^[^A-Za-z0-9_]+|[^A-Za-z0-9_]+$", "", term.strip("\"'")).casefold()
        not in _DISCOVERY_WORDS
    ]
    if content_terms:
        terms = content_terms
    search_text = " ".join(terms).strip()
    qualifier_text = " ".join(f"{key}:{value}" for key, value in qualifiers)
    api_query = _bounded_query(" ".join(item for item in (search_text, qualifier_text) if item))

    anchors = _literal_anchors(" ".join(terms) + (" " + regex_source if regex_source else ""))
    source_tokens = _source_shaped_tokens(remaining)
    concept_terms, structural_kind = (
        _query_signals(remaining, terms, source_tokens, regex_source)
    )
    variants: list[str] = []
    variant_kinds: list[str] = []

    def add_variant(candidate: str, kind: str) -> None:
        candidate = _bounded_query(candidate)
        if candidate and (candidate, kind) not in set(zip(variants, variant_kinds, strict=False)):
            variants.append(candidate)
            variant_kinds.append(kind)

    if regex_source:
        add_variant(regex_source, "regex")
        for alternative in _split_top_level_alternatives(regex_source):
            add_variant(alternative, "regex")
    else:
        add_variant(search_text or api_query, "lexical")
        qualified_source_tokens = [
            (value, leaf, parent, shape)
            for value, leaf, parent, shape in source_tokens
            if leaf.casefold() not in {"async", "def", "await", "class", "import", "export", "var", "func"}
        ]
        for value, leaf, _parent, _shape in qualified_source_tokens:
            add_variant(value, "symbol")
            if len(leaf) >= 4:
                add_variant(leaf, "symbol")
        compact = " ".join(dict.fromkeys(concept_terms[:3])) if len(concept_terms) >= 2 else ""
        if compact and compact != search_text:
            add_variant(compact, "lexical")
        if structural_kind or (mode == "docs" and not qualified_source_tokens):
            for identifier in _identifier_permutations(concept_terms):
                add_variant(identifier, "symbol")
    if deep and anchors:
        add_variant(f"{' '.join(anchors[:3])} code", "lexical")

    if not any(item for item in variants):
        warnings.append("No provider query variant could be produced.")
    if qualifiers and not search_text and not regex_source:
        warnings.append("Qualifier-only queries may be rejected by code providers.")
    for key, _ in qualifiers:
        if key in {"created", "pushed", "stars", "topics", "followers", "forks"}:
            warnings.append(f"Provider support for {key}: is limited; preserved for GitHub.")

    return QueryPlan(
        original_query=original,
        search_text=search_text,
        api_query=api_query,
        variants=tuple(variants[: max(1, max_variants)]),
        regex_source=regex_source,
        local_regex=local_regex,
        anchor_terms=tuple(anchors),
        qualifiers=qualifiers,
        warnings=tuple(dict.fromkeys(warnings)),
        variant_kinds=tuple(variant_kinds[: max(1, max_variants)]),
        source_tokens=tuple(source_tokens),
        concept_terms=tuple(concept_terms),
        structural_kind=structural_kind,
        exa_semantic_query="",
        mode=mode,
    )
