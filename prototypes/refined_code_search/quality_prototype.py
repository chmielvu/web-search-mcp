"""Disposable quality-first code-search prototype.

This is a clean-cut replacement sketch for the current result path. It keeps
provider adapters out of the quality decision: search candidates are converted
into source windows, scored on the source itself, collapsed by interval, and
only then returned. The ``--demo`` command is a manual exercise, not a test
suite.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

_MAX_WINDOW_LINES = 100
_DEFAULT_CONTEXT_LINES = 32


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
    repository: str
    path: str
    text: str

    @property
    def key(self) -> tuple[str, str]:
        return self.repository.casefold(), self.path.replace("\\", "/")

    @property
    def lines(self) -> tuple[str, ...]:
        return tuple(self.text.splitlines())


@dataclass(frozen=True, slots=True)
class Candidate:
    """A source-backed candidate before quality ranking and interval collapse."""

    source: FileSource
    window: LineRange
    match_lines: tuple[int, ...]
    retrieval_score: float = 0.0


@dataclass(frozen=True, slots=True)
class Result:
    """The only result representation exposed by the prototype."""

    repository: str
    path: str
    source_window: str
    line_start: int
    line_end: int
    match_lines: tuple[int, ...]
    score: float
    kind: str
    def to_dict(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "path": self.path,
            "source_window": self.source_window,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "match_lines": list(self.match_lines),
            "kind": self.kind,
        }


def _terms(query: str) -> tuple[list[str], str | None]:
    phrase_match = re.search(r'"([^"\n]+)"', query)
    phrase = phrase_match.group(1).casefold() if phrase_match else None
    raw = re.findall(r"[A-Za-z_][A-Za-z0-9_-]{1,}", query)
    stopwords = {"find", "show", "how", "where", "the", "with", "from", "code"}
    terms = list(dict.fromkeys(item.casefold() for item in raw if item.casefold() not in stopwords))
    return terms, phrase


def _line_has_term(line: str, term: str) -> bool:
    normalized = line.casefold()
    if "_" in term or "-" in term:
        return re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])",
            normalized,
        ) is not None
    identifiers = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", normalized)
    return any(term == identifier or term in re.split(r"[_-]", identifier) for identifier in identifiers)

def _line_has_phrase(line: str, phrase: str) -> bool:
    normalized = phrase.casefold()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", normalized):
        return _line_has_term(line, normalized)
    return normalized in line.casefold()


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith(("#", "//", "/*", "*", "<!--"))


def _is_declaration(line: str) -> bool:
    stripped = line.strip()
    prefixes = r"(?:(?:async|export|public|private|protected|static|pub)\s+)*"
    return re.match(
        rf"^{prefixes}(?:class|def|function|func|interface|struct|trait|enum)\b",
        stripped,
    ) is not None


def _is_document_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].casefold()
    return name.startswith(("readme", "changelog", "agents", "news")) or path.casefold().endswith(
        (".md", ".rst", ".txt")
    )


def _is_test_path(path: str) -> bool:
    return bool(re.search(r"(^|/)(test|tests|spec|specs)(/|$)|(^|[._-])(test|spec)[._-]", path, re.I))

def _is_vendored_path(path: str) -> bool:
    return bool(
        re.search(
            r"(^|/)(vendor|external|third_party|dist|build)(/|$)|(^|[._-])generated[._-]",
            path,
            re.I,
        )
    )


def _window_text(source: FileSource, window: LineRange) -> str:
    if window.end > len(source.lines):
        raise ValueError(f"window {window} exceeds {source.path}")
    return "\n".join(source.lines[window.start - 1 : window.end])


def _kind(source: FileSource, window: LineRange, match_lines: Iterable[int]) -> str:
    lines = source.lines
    matched = [lines[line - 1] for line in match_lines if 1 <= line <= len(lines)]
    if _is_document_path(source.path):
        return "documentation"
    if matched and all(_is_comment(line) for line in matched):
        return "comment"
    if _is_test_path(source.path):
        return "test"
    if any(_is_declaration(line) for line in matched):
        return "declaration"
    return "code"


def _score(source: FileSource, window: LineRange, match_lines: tuple[int, ...], query: str) -> float:
    terms, phrase = _terms(query)
    window_lines = source.lines[window.start - 1 : window.end]
    coverage = sum(
        any(_line_has_term(line, term) for line in window_lines) for term in terms
    ) / max(1, len(terms))
    score = coverage * 4.0
    if phrase and any(_line_has_phrase(line, phrase) for line in window_lines):
        score += 2.0
    if any(_is_declaration(source.lines[line - 1]) for line in match_lines):
        score += 1.5
    kind = _kind(source, window, match_lines)
    if kind == "documentation":
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
    return (
        all(any(_line_has_term(line, term) for line in lines) for term in terms)
        and (phrase is None or any(_line_has_phrase(line, phrase) for line in lines))
    )


def _expand_window(
    source: FileSource,
    match_lines: list[int],
    context: int,
    max_window_lines: int,
) -> LineRange:
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

    if end - start + 1 <= max_window_lines:
        return LineRange(start, end)
    end = min(len(source.lines), max(last_match, start) + max_window_lines - 1)
    start = max(1, end - max_window_lines + 1)
    if declaration_line is not None and declaration_line < start:
        start = declaration_line
        end = min(len(source.lines), start + max_window_lines - 1)
    return LineRange(start, end)
def _candidate_windows(
    source: FileSource,
    query: str,
    context: int,
    *,
    max_window_lines: int = _MAX_WINDOW_LINES,
) -> list[Candidate]:
    terms, phrase = _terms(query)
    if not terms:
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

    candidates: list[Candidate] = []
    for cluster in clusters:
        window = _expand_window(source, cluster, context, max_window_lines)
        match_lines = tuple(cluster)
        candidates.append(
            Candidate(
                source=source,
                window=window,
                match_lines=match_lines,
                retrieval_score=_score(source, window, match_lines, query),
            )
        )
    return candidates

def _better(left: Candidate, right: Candidate) -> Candidate:
    left_key = (left.retrieval_score, -left.window.width, -len(left.match_lines))
    right_key = (right.retrieval_score, -right.window.width, -len(right.match_lines))
    return left if left_key >= right_key else right


def _merge_candidates(left: Candidate, right: Candidate) -> Candidate:
    """Collapse containment; union only genuinely overlapping nearby windows."""

    winner = _better(left, right)
    if left.window.contains(right.window) or right.window.contains(left.window):
        window = winner.window
    else:
        window = left.window.union(right.window)
    visible_lines = tuple(sorted(set(line for line in (*left.match_lines, *right.match_lines) if window.start <= line <= window.end)))
    return Candidate(
        source=winner.source,
        window=window,
        match_lines=visible_lines,
        retrieval_score=max(left.retrieval_score, right.retrieval_score),
    )


def collapse_candidates(candidates: Iterable[Candidate]) -> list[Candidate]:
    """Return interval-disjoint, quality-ranked source windows."""

    def mergeable(left: Candidate, right: Candidate) -> bool:
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
        return (
            closest_match <= 5
            and left.window.union(right.window).width <= _MAX_WINDOW_LINES
        )

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
    buckets: dict[tuple[str, str], list[Candidate]] = {}
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

def search(
    files: Iterable[FileSource],
    query: str,
    *,
    context: int = _DEFAULT_CONTEXT_LINES,
    limit: int = 5,
    offset: int = 0,
    include_supporting: bool = False,
) -> list[Result]:
    if limit < 1:
        raise ValueError("limit must be positive")
    if offset < 0:
        raise ValueError("offset cannot be negative")
    candidates = [
        candidate
        for source in files
        for candidate in _candidate_windows(source, query, context)
    ]
    collapsed = collapse_candidates(candidates)
    complete = [
        item
        for item in collapsed
        if _window_has_all_terms(item.source, item.window, query)
    ]
    if complete:
        collapsed = complete
    if not include_supporting:
        primary = [
            item
            for item in collapsed
            if _kind(item.source, item.window, item.match_lines)
            not in {"documentation", "comment", "test"}
        ]
        if primary:
            collapsed = primary
    selected = collapsed[offset : offset + limit]
    return [
        Result(
            repository=item.source.repository,
            path=item.source.path,
            source_window=_window_text(item.source, item.window),
            line_start=item.window.start,
            line_end=item.window.end,
            match_lines=item.match_lines,
            score=item.retrieval_score,
            kind=_kind(item.source, item.window, item.match_lines),
        )
        for item in selected
    ]


async def _run_live_query(
    query_text: str,
    *,
    language: str | None,
    limit: int,
) -> dict[str, object]:
    """Call current providers and apply the quality path to canonical files."""

    import asyncio
    import os
    from urllib.parse import quote

    import httpx

    from kindly_web_search_mcp_server.tools.code_search.hydration import hydrate_sources
    from kindly_web_search_mcp_server.tools.code_search.models import (
        CodeSearchRequest,
        SearchBudget,
    )
    from kindly_web_search_mcp_server.tools.code_search.orchestrator import (
        search_exa,
        search_grepapp,
        search_github,
        search_sourcegraph,
    )
    from kindly_web_search_mcp_server.tools.code_search.query import build_query_plan
    from kindly_web_search_mcp_server.tools.code_search.ranking import rank_candidates as rank_hits

    request = CodeSearchRequest(
        query=query_text,
        language=language,
        max_results=limit * 4,
        budget=SearchBudget(
            max_results_per_search=limit * 4,
            max_hydrate_files=max(8, limit * 4),
            max_hydrated_chars_per_file=200_000,
            max_rerank_candidates=0,
            max_rerank_results=0,
        ),
    )
    plan = build_query_plan(
        query_text,
        language=language,
        max_variants=request.budget.max_query_variants,
        mode=request.mode,
    )
    provider_hit_counts: dict[str, int] = {}
    provider_diagnostics: list[dict[str, str]] = []
    failures: list[str] = []
    all_hits = []

    async def hydrate_with_rest(hit: object) -> tuple[bool, str | None]:
        repository = getattr(hit, "repository", None)
        path = getattr(hit, "path", None)
        if not isinstance(repository, str) or not isinstance(path, str):
            return False, None
        if getattr(hit, "hydrated_source", None):
            return False, None
        ref = getattr(hit, "commit_oid", None) or "HEAD"
        url = f"https://raw.githubusercontent.com/{repository}/{ref}/{quote(path, safe='/')}"
        headers = {"Accept": "text/plain"}
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = await http_client.get(url, headers=headers, timeout=30.0)
        except httpx.HTTPError as error:
            return False, f"GitHub REST hydration failed for {repository}:{path}: {error}"
        if response.status_code != 200:
            return False, f"GitHub REST hydration returned HTTP {response.status_code} for {repository}:{path}"
        text = response.text
        if not text:
            return False, f"GitHub REST hydration returned an empty file for {repository}:{path}"
        hit.hydrated_source = text.replace("\r\n", "\n")
        hit.hydrated_source_truncated = False
        return True, None

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0)
    ) as http_client:
        calls_with_client = (
            ("sourcegraph", search_sourcegraph(plan, request, http_client=http_client)),
            ("grep.app", search_grepapp(plan, request, http_client=http_client)),
            ("github", search_github(plan, request, http_client=http_client)),
            ("exa", search_exa(plan, request, http_client=http_client)),
        )
        responses = await asyncio.gather(
            *(operation for _, operation in calls_with_client),
            return_exceptions=True,
        )
        for (provider, _), response in zip(calls_with_client, responses):
            if isinstance(response, Exception):
                failures.append(f"{provider}: {type(response).__name__}: {response}")
                continue
            provider_hit_counts[provider] = len(response.hits)
            all_hits.extend(response.hits)
            provider_diagnostics.extend(
                {
                    "provider": provider,
                    "outcome": diagnostic.outcome,
                    "message": diagnostic.message,
                }
                for diagnostic in response.diagnostics
            )

        ranked = rank_hits(plan, all_hits, max_results=None)
        sources_hydrated, hydration_diagnostics = await hydrate_sources(
            ranked,
            http_client=http_client,
            max_files=request.budget.max_hydrate_files,
            max_chars_per_file=request.budget.max_hydrated_chars_per_file,
        )
        provider_diagnostics.extend(
            {
                "provider": "github",
                "outcome": diagnostic.outcome,
                "message": diagnostic.message,
            }
            for diagnostic in hydration_diagnostics
        )

        sources = sources_hydrated

    results = search(sources.values(), query_text, context=_DEFAULT_CONTEXT_LINES, limit=limit)
    return {
        "query": query_text,
        "providers_called": sorted(provider_hit_counts),
        "retrieved_hits": sum(provider_hit_counts.values()),
        "hydrated_files": len(sources),
        "provider_failures": failures,
        "results": [result.to_dict() for result in results],
    }

def _live(query_text: str, *, language: str | None, limit: int) -> None:
    import asyncio

    print(json.dumps(asyncio.run(_run_live_query(query_text, language=language, limit=limit)), indent=2))


def _demo_sources() -> list[FileSource]:
    return [
        FileSource(
            "acme/search",
            "src/retry.py",
            "from time import sleep\n\n"
            "def retry_with_backoff(operation):\n"
            "    for attempt in range(5):\n"
            "        try:\n"
            "            return operation()\n"
            "        except TimeoutError:\n"
            "            sleep(2 ** attempt)\n",
        ),
        FileSource(
            "acme/search",
            "README.md",
            "# Retry\n\nUse retry with exponential backoff for transient errors.\n",
        ),
        FileSource(
            "acme/search",
            "tests/test_retry.py",
            "def test_retry_with_backoff():\n    assert retry_with_backoff(lambda: 1) == 1\n",
        ),
    ]


def _demo() -> None:
    source = _demo_sources()[0]
    raw = [
        Candidate(source, LineRange(3, 3), (3,), 8.0),
        Candidate(source, LineRange(3, 5), (3,), 7.0),
        Candidate(source, LineRange(1, 9), (3,), 5.0),
        Candidate(source, LineRange(6, 9), (7,), 6.0),
    ]
    collapsed = collapse_candidates(raw)
    results = search(_demo_sources(), "retry backoff", context=1, limit=5)
    print(
        json.dumps(
            {
                "manual_scenario": "quality-first source windows",
                "containment_input_windows": [[item.window.start, item.window.end] for item in raw],
                "collapsed_windows": [[item.window.start, item.window.end] for item in collapsed],
                "search_results": [result.to_dict() for result in results],
            },
            indent=2,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Quality-first code-search prototype")
    parser.add_argument("--demo", action="store_true", help="run the deterministic local scenario")
    parser.add_argument(
        "--live-query",
        metavar="QUERY",
        help="call the current code-search providers before applying the quality path",
    )
    parser.add_argument("--language", default=None, help="optional provider language qualifier")
    parser.add_argument("--limit", type=int, default=5, help="number of final quality windows")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    if bool(args.demo) == bool(args.live_query):
        parser.error("choose exactly one of --demo or --live-query")
    if args.live_query:
        _live(args.live_query, language=args.language, limit=args.limit)
    else:
        _demo()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
