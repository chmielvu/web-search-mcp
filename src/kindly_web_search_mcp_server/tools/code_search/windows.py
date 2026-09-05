"""Standalone source window engine for bounded, syntax-aware code extraction."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .models import CodeSearchHit, build_location_metadata
from .query import QueryPlan

LOGGER = logging.getLogger(__name__)

_MAX_WINDOW_LINES = 100
_DEFAULT_CONTEXT_LINES = 32

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "can",
    "code",
    "does",
    "find",
    "from",
    "how",
    "i",
    "in",
    "is",
    "of",
    "on",
    "people",
    "show",
    "the",
    "to",
    "use",
    "what",
    "where",
    "with",
}


@dataclass(frozen=True, slots=True)
class LineRange:
    """Inclusive one-based line interval."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 1 or self.end < self.start:
            raise ValueError(f"invalid line range: {self.start}-{self.end}")

    def contains(self, other: LineRange) -> bool:
        return self.start <= other.start and self.end >= other.end

    def touches(self, other: LineRange) -> bool:
        return self.start <= other.end + 1 and other.start <= self.end + 1

    def union(self, other: LineRange) -> LineRange:
        if not self.touches(other):
            raise ValueError(f"disjoint ranges: {self} and {other}")
        return LineRange(min(self.start, other.start), max(self.end, other.end))

    @property
    def width(self) -> int:
        return self.end - self.start + 1


@dataclass(frozen=True, slots=True)
class FileSource:
    """Canonical source content with one immutable line split per file."""

    repository: str
    path: str
    text: str
    _line_cache: tuple[str, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_line_cache", tuple(self.text.splitlines()))

    @property
    def key(self) -> tuple[str, str]:
        return self.repository.casefold(), self.path.replace("\\", "/").casefold()

    @property
    def lines(self) -> tuple[str, ...]:
        return self._line_cache


@dataclass(frozen=True, slots=True)
class CandidateWindow:
    """A source-backed candidate window before final ranking and collapse."""

    source: FileSource
    window: LineRange
    match_lines: tuple[int, ...]
    retrieval_score: float = 0.0


def _terms(query: str) -> tuple[list[str], str | None]:
    """Extract keywords and quoted phrases from a query string."""
    phrase_match = re.search(r'"([^"\n]+)"', query)
    phrase = phrase_match.group(1).strip() if phrase_match else None
    cleaned = re.sub(r'"[^"\n]+"', " ", query)
    raw_terms = re.findall(r"[A-Za-z0-9_.-]+", cleaned)
    terms = [
        term.casefold()
        for term in raw_terms
        if len(term) >= 2 and term.casefold() not in _STOPWORDS
    ]
    return terms, phrase


def _line_has_term(line: str, term: str) -> bool:
    """Check word-boundary term match, respecting compound identifiers."""
    normalized = line.casefold()
    if term not in normalized:
        return False
    identifiers = [identifier.casefold() for identifier in re.findall(r"[A-Za-z0-9_]+", line)]
    return any(
        term == identifier or term in re.split(r"[_-]", identifier) for identifier in identifiers
    )


def _line_has_phrase(line: str, phrase: str) -> bool:
    """Check phrase match in a line."""
    normalized = phrase.casefold()
    words = [word.casefold() for word in re.findall(r"[A-Za-z0-9_]+", phrase)]
    if words and all(word in line.casefold() for word in words):
        return True
    return normalized in line.casefold()


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith(("#", "//", "/*", "*", "<!--"))


def _is_declaration(line: str) -> bool:
    stripped = line.strip()
    return (
        re.match(
            r"^(?:pub\s+)?(?:async\s+)?(?:def|class|function|interface|struct|trait|enum)\b|"
            r"^(?:export\s+)?(?:default\s+)?(?:class|function|interface|type)\b",
            stripped,
        )
        is not None
    )


def _is_document_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].casefold()
    return name.endswith(
        (".md", ".rst", ".txt", ".adoc", ".markdown", ".json", ".yaml", ".yml", ".toml")
    )


def _is_test_path(path: str) -> bool:
    return bool(
        re.search(r"(^|/)(test|tests|spec|specs)(/|$)|(^|[._-])(test|spec)[._-]", path, re.I)
    )


def _is_vendored_path(path: str) -> bool:
    return bool(
        re.search(
            r"(^|/)(vendor|node_modules|\.git|dist|build|third_party|target|site-packages)(/|$)",
            path,
            re.I,
        )
    )


def _kind(source: FileSource, window: LineRange, match_lines: Iterable[int]) -> str:
    """Classify window evidence kind."""
    lines = source.lines
    if _is_document_path(source.path):
        return "documentation"
    if _is_test_path(source.path):
        return "test"
    match_set = set(match_lines)
    window_lines = [lines[idx - 1] for idx in match_set if 1 <= idx <= len(lines)]
    if window_lines and all(_is_comment(line) for line in window_lines):
        return "comment"
    if any(_is_declaration(lines[idx - 1]) for idx in match_set if 1 <= idx <= len(lines)):
        return "declaration"
    return "code"


def _score(
    source: FileSource,
    window: LineRange,
    match_lines: tuple[int, ...],
    query: str,
    regex: re.Pattern[str] | None = None,
) -> float:
    """Calculate quality score for a window."""
    terms, phrase = _terms(query)
    score = 0.0
    matched_text = "\n".join(
        source.lines[line - 1] for line in match_lines if 1 <= line <= len(source.lines)
    )
    if regex is not None:
        regex_matches = len(regex.findall(matched_text))
        score += min(4.0, regex_matches * 1.5)
    if terms:
        covered = sum(
            1
            for term in terms
            if any(
                _line_has_term(line, term) for line in source.lines[window.start - 1 : window.end]
            )
        )
        score += (covered / len(terms)) * 4.0
    if phrase and _line_has_phrase(matched_text, phrase):
        score += 2.0
    kind = _kind(source, window, match_lines)
    if kind == "declaration":
        score += 1.5
    elif kind == "documentation":
        score -= 6.0
    elif kind == "test":
        score -= 4.0
    elif kind == "comment":
        score -= 5.0
    if _is_vendored_path(source.path):
        score -= 2.0
    score += 1.0 / max(1, window.width)
    return score


def _window_has_all_terms(source: FileSource, window: LineRange, query: str) -> bool:
    terms, phrase = _terms(query)
    lines = source.lines[window.start - 1 : window.end]
    if phrase and not any(_line_has_phrase(line, phrase) for line in lines):
        return False
    return all(any(_line_has_term(line, term) for line in lines) for term in terms)


def _expand_window(
    source: FileSource,
    match_lines: list[int],
    context: int,
    max_window_lines: int,
) -> LineRange:
    """Expand match lines to enclosing declaration or balanced block, bounded by max_window_lines."""
    first_match = match_lines[0]
    last_match = match_lines[-1]

    declaration_line = next(
        (
            line_number
            for line_number in range(first_match, 0, -1)
            if _is_declaration(source.lines[line_number - 1])
        ),
        None,
    )
    if declaration_line is None:
        start = max(1, first_match - context)
        end = min(len(source.lines), last_match + context)
    else:
        declaration = source.lines[declaration_line - 1]
        declaration_indent = len(declaration) - len(declaration.lstrip())
        header_end = declaration_line
        if not declaration.rstrip().endswith((":", "{")):
            for line_number in range(declaration_line + 1, len(source.lines) + 1):
                header_end = line_number
                if source.lines[line_number - 1].rstrip().endswith((":", "{")):
                    break
        body_end = max(last_match, header_end)
        brace_depth = sum(
            source.lines[line_number - 1].count("{") - source.lines[line_number - 1].count("}")
            for line_number in range(declaration_line, header_end + 1)
        )
        if brace_depth > 0:
            for line_number in range(header_end + 1, len(source.lines) + 1):
                body_end = line_number
                line = source.lines[line_number - 1]
                brace_depth += line.count("{") - line.count("}")
                if brace_depth <= 0:
                    break
        else:
            saw_body = False
            for line_number in range(header_end + 1, len(source.lines) + 1):
                line = source.lines[line_number - 1]
                if not line.strip():
                    if saw_body:
                        body_end = line_number
                    continue
                current_indent = len(line) - len(line.lstrip())
                if current_indent <= declaration_indent and line_number > last_match:
                    break
                saw_body = True
                body_end = line_number
        surrounding = max(8, context // 2)
        start = max(1, declaration_line - surrounding)
        end = min(len(source.lines), body_end + surrounding)

    if end - start + 1 > max_window_lines:
        end = min(len(source.lines), max(last_match, start) + max_window_lines - 1)
        start = max(1, end - max_window_lines + 1)
        if declaration_line is not None and declaration_line < start:
            start = declaration_line
            end = min(len(source.lines), start + max_window_lines - 1)
    while start < first_match and not source.lines[start - 1].strip():
        start += 1
    while end > last_match and not source.lines[end - 1].strip():
        end -= 1
    return LineRange(start, end)


def _candidate_windows(
    source: FileSource,
    query: str,
    context: int,
    *,
    regex: re.Pattern[str] | None = None,
    max_window_lines: int = _MAX_WINDOW_LINES,
) -> list[CandidateWindow]:
    """Find candidate match clusters in a source file."""
    if regex is not None:
        matched = tuple(
            index for index, line in enumerate(source.lines, 1) if regex.search(line) is not None
        )
    else:
        terms, phrase = _terms(query)
        if not terms and not phrase:
            return []
        matched = tuple(
            index
            for index, line in enumerate(source.lines, 1)
            if any(_line_has_term(line, term) for term in terms)
            or (phrase is not None and _line_has_phrase(line, phrase))
        )
    if not matched:
        return []

    match_gap = max(1, context * 2 + 1)
    max_match_span = max(0, max_window_lines - 1 - 2 * context)
    clusters: list[list[int]] = []
    for line in matched:
        if (
            clusters
            and line - clusters[-1][-1] <= match_gap
            and line - clusters[-1][0] <= max_match_span
        ):
            clusters[-1].append(line)
        else:
            clusters.append([line])

    candidates: list[CandidateWindow] = []
    for cluster in clusters:
        window = _expand_window(source, cluster, context, max_window_lines)
        match_lines = tuple(cluster)
        candidates.append(
            CandidateWindow(
                source=source,
                window=window,
                match_lines=match_lines,
                retrieval_score=_score(source, window, match_lines, query, regex=regex),
            )
        )
    return candidates


def _better(left: CandidateWindow, right: CandidateWindow) -> CandidateWindow:
    left_key = (left.retrieval_score, -left.window.width, -len(left.match_lines))
    right_key = (right.retrieval_score, -right.window.width, -len(right.match_lines))
    return left if left_key >= right_key else right


def _merge_candidates(left: CandidateWindow, right: CandidateWindow) -> CandidateWindow:
    """Collapse containment; union only genuinely overlapping nearby windows."""
    winner = _better(left, right)
    if left.window.contains(right.window) or right.window.contains(left.window):
        window = winner.window
    else:
        window = left.window.union(right.window)
    visible_lines = tuple(
        sorted(
            set(
                line
                for line in (*left.match_lines, *right.match_lines)
                if window.start <= line <= window.end
            )
        )
    )
    return CandidateWindow(
        source=winner.source,
        window=window,
        match_lines=visible_lines,
        retrieval_score=max(left.retrieval_score, right.retrieval_score),
    )


def collapse_candidates(candidates: Iterable[CandidateWindow]) -> list[CandidateWindow]:
    """Return interval-disjoint, quality-ranked source windows."""

    def mergeable(left: CandidateWindow, right: CandidateWindow) -> bool:
        if not left.window.touches(right.window):
            return False
        if left.window.contains(right.window) or right.window.contains(left.window):
            return True
        if left.window.end < right.window.start or right.window.end < left.window.start:
            return False
        if not left.match_lines or not right.match_lines:
            return False
        closest_match = min(
            abs(left_line - right_line)
            for left_line in left.match_lines
            for right_line in right.match_lines
        )
        return closest_match <= 5 and left.window.union(right.window).width <= _MAX_WINDOW_LINES

    ordered = sorted(
        candidates,
        key=lambda item: (
            -item.retrieval_score,
            item.source.repository.casefold(),
            item.source.path,
            item.window.start,
            item.window.end,
        ),
    )
    buckets: dict[tuple[str, str], list[CandidateWindow]] = {}
    for candidate in ordered:
        bucket = buckets.setdefault(candidate.source.key, [])
        for index, existing in enumerate(bucket):
            if not mergeable(existing, candidate):
                continue
            bucket[index] = _merge_candidates(existing, candidate)
            current = bucket[index]
            cursor = 0
            while cursor < len(bucket):
                if cursor == index:
                    cursor += 1
                    continue
                if mergeable(current, bucket[cursor]):
                    current = _merge_candidates(current, bucket.pop(cursor))
                    bucket[index] = current
                else:
                    cursor += 1
                break
            break
        else:
            bucket.append(candidate)

    collapsed = [item for bucket in buckets.values() for item in bucket]
    collapsed.sort(
        key=lambda item: (
            -item.retrieval_score,
            item.source.repository.casefold(),
            item.source.path,
            item.window.start,
        )
    )
    return collapsed


def extract_source_windows(
    plan: QueryPlan,
    file_sources: dict[tuple[str, str], FileSource],
    candidate_hits: list[CodeSearchHit],
    max_results: int,
    *,
    context: int = _DEFAULT_CONTEXT_LINES,
) -> list[CodeSearchHit]:
    """Extract, score, collapse, and materialize clean CodeSearchHit instances."""
    if max_results < 1:
        return []

    # Map candidate hits by canonical key
    hits_by_key: dict[tuple[str, str], list[CodeSearchHit]] = {}
    for hit in candidate_hits:
        if hit.repository and hit.path:
            key = (hit.repository.casefold(), hit.path.replace("\\", "/").casefold())
            hits_by_key.setdefault(key, []).append(hit)

    query_text = plan.search_text or plan.original_query
    regex = plan.local_regex

    all_candidates: list[CandidateWindow] = []
    for source in file_sources.values():
        windows = _candidate_windows(
            source,
            query_text,
            context,
            regex=regex,
            max_window_lines=_MAX_WINDOW_LINES,
        )
        all_candidates.extend(windows)

    if not all_candidates:
        return []

    collapsed = collapse_candidates(all_candidates)

    # Filter by term completeness if any window has all terms
    if regex is None:
        complete = [
            item
            for item in collapsed
            if _window_has_all_terms(item.source, item.window, query_text)
        ]
        if complete:
            collapsed = complete
    else:
        collapsed = [
            item
            for item in collapsed
            if regex.search("\n".join(item.source.lines[item.window.start - 1 : item.window.end]))
            is not None
        ]
    # Prefer code/declaration windows over doc/test/comment windows
    primary = [
        item
        for item in collapsed
        if _kind(item.source, item.window, item.match_lines)
        not in {"documentation", "comment", "test"}
    ]
    if primary:
        collapsed = primary

    selected = collapsed[:max_results]

    results: list[CodeSearchHit] = []
    for candidate in selected:
        source = candidate.source
        window = candidate.window
        source_window = "\n".join(source.lines[window.start - 1 : window.end])
        key = source.key
        proto_hits = hits_by_key.get(key, [])
        first_proto = proto_hits[0] if proto_hits else None

        kind = _kind(source, window, candidate.match_lines)
        provider = first_proto.provider if first_proto else "github"
        sha = first_proto.sha if first_proto else None
        commit_oid = first_proto.commit_oid if first_proto else None
        symbols = first_proto.symbols if first_proto else []
        query_variant = first_proto.query_variant if first_proto else None
        sha_str = sha or "main"
        if "/" in source.repository:
            url = f"https://github.com/{source.repository}/blob/{sha_str}/{source.path}#L{window.start}-L{window.end}"
        elif first_proto and first_proto.url:
            base_url = re.sub(r"#.*$", "", first_proto.url)
            url = f"{base_url}#L{window.start}-L{window.end}"
        else:
            url = f"https://github.com/{source.repository}/blob/{sha_str}/{source.path}#L{window.start}-L{window.end}"

        source_metadata: dict[str, Any] = {}
        if first_proto and first_proto.source_metadata:
            source_metadata.update(first_proto.source_metadata)
        source_metadata["source_window_start"] = window.start
        source_metadata["source_window_end"] = window.end
        source_metadata["full_source_chars"] = len(source.text)

        providers = [provider]
        if first_proto and first_proto.source_metadata.get("providers"):
            for p in first_proto.source_metadata["providers"]:
                if p not in providers:
                    providers.append(p)
        source_metadata["providers"] = providers

        location = build_location_metadata(
            repository=source.repository,
            path=source.path,
            url=url,
            line_start=window.start,
            line_end=window.end,
            revision=sha or commit_oid,
            match_data_available=True,
        )

        hit = CodeSearchHit(
            repository=source.repository,
            path=source.path,
            url=url,
            provider=provider,
            sha=sha,
            commit_oid=commit_oid,
            query_variant=query_variant,
            result_kind="code_match",
            evidence_role=kind,
            source_window=source_window,
            line_start=window.start,
            line_end=window.end,
            match_lines=list(candidate.match_lines),
            symbols=symbols,
            score=candidate.retrieval_score,
            score_components={"window_quality": candidate.retrieval_score},
            reasons=[f"source window: {kind} ({window.start}-{window.end})"],
            source_metadata=source_metadata,
            location=location,
        )
        results.append(hit)

    return results
