"""Disposable refined public-code search prototype.

This is intentionally separate from the production MCP tool. It accepts one
mode-free request and lets the backend select and combine retrieval channels:

* code-aware query variants;
* Sourcegraph-native queries and line evidence;
* repository discovery enriched with README text;
* repository discovery followed by code proof;
* implementation-oriented ranking and compact agent output.

It uses only httpx and the authenticated gh session already available on the
machine.  It is an experiment, not a production dependency.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import os
import re
import subprocess
import time
import tokenize
from dataclasses import asdict, dataclass, field, replace
from typing import Any

import httpx

try:
    from .contracts import SearchInput, SearchResponse
except ImportError:  # pragma: no cover - direct script execution
    from contracts import SearchInput, SearchResponse


REST = "https://api.github.com"
GRAPHQL = "https://api.github.com/graphql"
SOURCEGRAPH = "https://sourcegraph.com/.api/graphql"
GREPAPP = "https://grep.app/api/search"
EXA_SEARCH = "https://api.exa.ai/search"
UA = "refined-code-search-prototype/1"

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "can",
    "does",
    "find",
    "for",
    "give",
    "how",
    "i",
    "implement",
    "implemented",
    "implementation",
    "implementations",
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
    "with",
    "where",
}
QUALIFIER_KEYS = {
    "extension",
    "filename",
    "in",
    "language",
    "org",
    "path",
    "repo",
    "ref",
    "user",
}
CODE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".dart",
    ".ex",
    ".exs",
    ".fs",
    ".fsx",
    ".go",
    ".groovy",
    ".h",
    ".hpp",
    ".hs",
    ".java",
    ".jl",
    ".js",
    ".jsx",
    ".kt",
    ".lua",
    ".mjs",
    ".nim",
    ".php",
    ".pl",
    ".py",
    ".rb",
    ".rs",
    ".scala",
    ".sh",
    ".sql",
    ".swift",
    ".ts",
    ".tsx",
    ".vue",
    ".zig",
}
CODE_QUERY_MARKERS = {
    "async",
    "await",
    "call",
    "class",
    "def",
    "export",
    "from",
    "function",
    "import",
    "interface",
    "method",
    "module",
    "namespace",
    "struct",
    "use",
}
DOC_NAMES = {"readme", "news", "changelog", "prd", "claude", "agents"}
NOISE_PATH_PARTS = (
    "/vendor/",
    "/node_modules/",
    "/third_party/",
    "/dist/",
    "/build/",
    "/experiments/",
    "/results/",
    "/fixtures/",
    "/snapshots/",
)
NOISE_FILE_NAMES = {
    "composer.json",
    "composer.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "go.sum",
}
PROOF_GENERIC_TERMS = {
    "code",
    "find",
    "github",
    "implementation",
    "java",
    "javascript",
    "library",
    "mcp",
    "python",
    "repo",
    "repository",
    "rust",
    "search",
    "typescript",
}
ROLE_EXPANSIONS: dict[str, set[str]] = {
    "implementation": {"declaration", "export", "callsite", "code"},
    "identifier": {"declaration", "export", "callsite", "code"},
    "export": {"export", "declaration"},
    "code": {"code", "declaration", "export", "callsite"},
}


@dataclass
class Plan:
    original: str
    terms: list[str]
    anchors: list[str]
    regex: str | None
    repositories: list[str]
    language: str | None
    path: str | None
    filename: str | None
    extension: str | None
    include_paths: list[str] = field(default_factory=list)
    exclude_paths: list[str] = field(default_factory=list)
    ref: str | None = None
    case_sensitive: bool = False
    whole_word: bool = False
    source_tokens: list[dict[str, str]] = field(default_factory=list)
    concept_terms: list[str] = field(default_factory=list)
    structural_kind: str | None = None
    discovery_score: float = 0.0
    semantic_score: float = 0.0
    documentation_score: float = 0.0
    channel: str = "lexical"
    warnings: list[str] = field(default_factory=list)

    @property
    def natural_language(self) -> bool:
        return (
            len(self.terms) > 2
            and not any(re.search(r"[._:/(){}\[\]]", item) for item in self.terms)
            and not any(term.casefold() in CODE_QUERY_MARKERS for term in self.terms)
        )

    @property
    def query_interpretation(self) -> dict[str, Any]:
        return {
            "source_tokens": self.source_tokens,
            "concept_terms": self.concept_terms,
            "structural_kind": self.structural_kind,
            "discovery_score": round(self.discovery_score, 3),
            "semantic_score": round(self.semantic_score, 3),
            "documentation_score": round(self.documentation_score, 3),
            "regex": self.regex,
            "backend_channels": sorted(
                {
                    "lexical",
                    *("symbol" for _ in self.source_tokens),
                    *("regex" for _ in [self.regex] if self.regex),
                    *("semantic" for _ in [self.semantic_score] if self.semantic_score >= 0.5),
                    *("repository" for _ in [self.discovery_score] if self.discovery_score >= 0.5),
                    *(
                        "documentation"
                        for _ in [self.documentation_score]
                        if self.documentation_score >= 0.5
                    ),
                }
            ),
        }


@dataclass
class Hit:
    repository: str | None
    path: str | None
    url: str
    provider: str
    title: str | None = None
    snippet: str = ""
    line_start: int | None = None
    line_end: int | None = None
    revision: str | None = None
    blob_sha: str | None = None
    hydrated_commit: str | None = None
    match_lines: list[int] = field(default_factory=list)
    match_spans: list[dict[str, Any]] = field(default_factory=list)
    symbols: list[dict[str, Any]] = field(default_factory=list)
    match_line: int | None = None
    providers: list[str] = field(default_factory=list)
    provider_queries: list[str] = field(default_factory=list)
    match_kind: str = "unknown"
    verified: bool = False
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    provider_query: str | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RepoCandidate:
    repository: str
    url: str
    description: str
    readme_preview: str
    topics: list[str]
    language: str | None
    stars: int
    pushed_at: str | None
    relevance: float = 0.0
    proof_hits: int = 0
    proof_requested: bool = False
    verified: bool = False
    proof_paths: list[str] = field(default_factory=list)
    proof_query: str | None = None


def normalize_repo(value: str) -> str:
    value = value.strip().rstrip("/")
    value = re.sub(r"^https?://github\.com/", "", value, flags=re.I)
    value = value.removesuffix(".git")
    parts = [part for part in value.split("/") if part]
    if len(parts) != 2 or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in parts):
        raise ValueError(f"repository must be owner/repo: {value!r}")
    return "/".join(parts)


def _tokens(text: str) -> list[str]:
    return re.findall(r'"[^"\n]*"|\S+', text)


def _split_identifier(value: str) -> list[str]:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    value = re.sub(r"[^A-Za-z0-9]+", " ", value)
    return [part for part in value.split() if len(part) >= 3]


_QUALIFIED_IDENTIFIER = re.compile(
    r"(?<![\w.])(?P<value>[A-Za-z_][A-Za-z0-9_]*(?:(?:::|\.|#)[A-Za-z_][A-Za-z0-9_]*)+)"
)
_SOURCE_IDENTIFIER = re.compile(
    r"(?<![\w.])(?P<value>__[A-Za-z0-9_]+__|[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+|"
    r"[a-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*|[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+)(?![\w.])"
)
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
_DISCOVERY_WORDS = {
    "library",
    "libraries",
    "project",
    "projects",
    "repo",
    "repos",
    "repositories",
    "repository",
}
_DOC_WORDS = {"api", "documentation", "docs", "guide", "reference", "usage"}


def _source_shaped_tokens(query: str) -> list[dict[str, str]]:
    """Extract identifiers without mistaking prose or filenames for symbols."""

    values: dict[str, dict[str, str]] = {}
    for shape, pattern in (
        ("qualified", _QUALIFIED_IDENTIFIER),
        ("identifier", _SOURCE_IDENTIFIER),
    ):
        for match in pattern.finditer(query):
            value = match.group("value").rstrip("(")
            if (
                "." in value
                and value.rsplit(".", 1)[-1].casefold()
                in {suffix.removeprefix(".") for suffix in CODE_SUFFIXES}
                and "::" not in value
                and "#" not in value
            ):
                continue
            separator = "::" if "::" in value else ("#" if "#" in value else ".")
            parts = value.split(separator)
            leaf = parts[-1]
            parent = separator.join(parts[:-1]) if len(parts) > 1 else ""
            key = value.casefold()
            values.setdefault(
                key,
                {"value": value, "leaf": leaf, "parent": parent, "shape": shape},
            )
            if len(values) >= 6:
                return list(values.values())
    return list(values.values())


def _query_signals(
    query: str, terms: list[str], source_tokens: list[dict[str, str]], regex: str | None
) -> tuple[list[str], str | None, float, float, float]:
    lowered = query.casefold()
    words = {item.casefold() for item in re.findall(r"[A-Za-z][A-Za-z0-9_-]*", query)}
    concepts = [
        re.sub(r"^[^A-Za-z0-9_]+|[^A-Za-z0-9_]+$", "", item.strip("\"'"))
        for item in terms
        if item.strip("\"'")
        and item.casefold().strip("\"'") not in _STRUCTURAL_WORDS | _DISCOVERY_WORDS
    ]
    concepts = [item for item in concepts if item]
    structural_kind = next((word for word in _STRUCTURAL_WORDS if word in words), None)
    syntax_evidence = bool(regex or source_tokens or re.search(r"[(){}\[\]]|::|=>|->", query))
    prose_evidence = len(concepts) >= 2 or bool(
        re.search(r"\b(find|show|where|how|implementation)\b", lowered)
    )
    discovery_evidence = len(words & _DISCOVERY_WORDS)
    if re.search(r"\b(existing|open[ -]source|mass|many|examples?)\b", lowered):
        discovery_evidence += 1
    docs_evidence = len(words & _DOC_WORDS)
    semantic_score = min(1.0, 0.25 * len(concepts) + (0.25 if prose_evidence else 0.0))
    if syntax_evidence and not prose_evidence:
        semantic_score *= 0.35
    discovery_score = min(1.0, 0.5 * discovery_evidence)
    documentation_score = min(1.0, 0.5 * docs_evidence)
    return concepts, structural_kind, discovery_score, semantic_score, documentation_score


def _identifier_permutations(terms: list[str]) -> list[str]:
    words = [part for term in terms for part in _split_identifier(term.strip("\"'"))]
    words = list(dict.fromkeys(word for word in words if word.casefold() not in STOPWORDS))
    if not 2 <= len(words) <= 4:
        return []
    snake = "_".join(word.casefold() for word in words)
    camel = words[0].casefold() + "".join(word[:1].upper() + word[1:] for word in words[1:])
    pascal = "".join(word[:1].upper() + word[1:] for word in words)
    return list(dict.fromkeys((snake, camel, pascal)))


def _anchors(terms: list[str], regex: str | None) -> list[str]:
    raw = " ".join(terms) + (f" {regex}" if regex else "")
    raw = re.sub(r"\\(.)", r"\1", raw)
    raw = re.sub(r"[()[\].*+?{}^$|]", " ", raw)
    values: dict[str, str] = {}
    for term in terms:
        cleaned = term.strip("\"'")
        if len(cleaned) >= 3 and cleaned.casefold() not in STOPWORDS:
            values.setdefault(cleaned.casefold(), cleaned)
    for term in re.split(r"\s+", raw):
        for part in _split_identifier(term.strip("\"'")) or [term.strip("\"'")]:
            if len(part) >= 3 and part.casefold() not in STOPWORDS:
                values.setdefault(part.casefold(), part)
    return sorted(values.values(), key=lambda x: (-len(x), x.casefold()))


def proof_plans(plan: Plan) -> list[Plan]:
    """Return strict-then-relaxed plans for proving a discovered repository."""
    if plan.regex or len(plan.terms) <= 1:
        return [plan]
    variants = [plan]
    meaningful = [
        term for term in plan.terms if term.strip("\"'").casefold() not in PROOF_GENERIC_TERMS
    ]
    if not meaningful:
        meaningful = [plan.terms[-1]]
    pair_variants = [meaningful[:2], [meaningful[0], meaningful[-1]]]
    for terms in pair_variants:
        terms = list(dict.fromkeys(terms))
        if not terms or terms == plan.terms:
            continue
        variants.append(replace(plan, terms=terms, anchors=_anchors(terms, None)))
    return variants


def search_plans(plan: Plan) -> list[tuple[str, Plan]]:
    """Build bounded evidence channels from the backend query interpretation."""

    variants: list[tuple[str, Plan]] = [
        (
            "regex" if plan.regex else "primary",
            replace(plan, channel="regex" if plan.regex else "lexical"),
        )
    ]
    if plan.regex:
        return variants

    seen = {("lexical", tuple(plan.terms))}
    for token in plan.source_tokens:
        for label, value in (("qualified", token["value"]), ("symbol", token["leaf"])):
            terms = [value]
            key = ("symbol", tuple(terms))
            if value and key not in seen:
                variants.append(
                    (
                        f"{label}-{len(variants)}",
                        replace(plan, terms=terms, anchors=[value], channel="symbol"),
                    )
                )
                seen.add(key)

    meaningful = [
        term for term in plan.concept_terms if term.casefold() not in PROOF_GENERIC_TERMS
    ] or plan.concept_terms
    if len(meaningful) >= 2:
        compact_terms = list(dict.fromkeys(meaningful[:3]))
        key = ("lexical", tuple(compact_terms))
        if key not in seen:
            variants.append(
                (
                    "compact",
                    replace(
                        plan,
                        terms=compact_terms,
                        anchors=_anchors(compact_terms, None),
                        channel="lexical",
                    ),
                )
            )
            seen.add(key)

    if plan.structural_kind or (
        not plan.source_tokens
        and plan.semantic_score >= 0.5
        and not re.search(r"[(){}\[\]]|::|=>|->", plan.original)
    ):
        for identifier in _identifier_permutations(meaningful)[:3]:
            key = ("symbol", (identifier,))
            if key not in seen:
                variants.append(
                    (
                        f"identifier-{len(variants)}",
                        replace(
                            plan,
                            terms=[identifier],
                            anchors=[identifier],
                            channel="symbol",
                        ),
                    )
                )
                seen.add(key)
    return variants[:6]


def plan_query(
    query: str,
    *,
    repositories: list[str],
    language: str | None,
    path: str | None,
    filename: str | None,
    extension: str | None,
    regexp: bool,
    include_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    ref: str | None = None,
    case_sensitive: bool = False,
    whole_word: bool = False,
) -> Plan:
    original = query.strip()
    regex: str | None = None
    remaining = original
    match = re.search(r"(?:^|\s)/((?:[^/\\\n]|\\.)+)/(?:[imsu]*)(?=$|\s)", original)
    if match:
        regex = match.group(1)
        remaining = f"{original[: match.start()]} {original[match.end() :]}".strip()
    elif regexp:
        regex = original
    if regex:
        try:
            re.compile(regex)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc

    parsed: dict[str, list[str]] = {}
    free: list[str] = []
    for token in _tokens(remaining):
        qualifier = re.match(r"^(\w+):(.+)$", token)
        if qualifier and qualifier.group(1).casefold() in QUALIFIER_KEYS:
            parsed.setdefault(qualifier.group(1).casefold(), []).append(qualifier.group(2))
        else:
            free.append(token)

    explicit = {
        "language": language,
        "path": path,
        "filename": filename,
        "extension": extension,
        "ref": ref,
    }
    warnings: list[str] = []
    for key, value in explicit.items():
        if value and key in parsed and value not in parsed[key]:
            raise ValueError(f"query qualifier {key}: conflicts with explicit {key}={value!r}")
        if value:
            parsed.setdefault(key, []).append(value)

    scopes = [normalize_repo(item) for item in repositories]
    for item in parsed.get("repo", []):
        normalized = normalize_repo(item)
        if scopes and normalized not in scopes:
            raise ValueError(f"query qualifier repo:{normalized} conflicts with repositories")
        if normalized not in scopes:
            scopes.append(normalized)

    def normalized_word(item: str) -> str:
        return re.sub(r"^[^A-Za-z0-9_]+|[^A-Za-z0-9_]+$", "", item.strip("\"'"))

    terms = [item for item in free if normalized_word(item).casefold() not in STOPWORDS]
    if not terms and free:
        terms = free[:3]
    content_terms = [item for item in terms if item.strip("\"'").casefold() not in _DISCOVERY_WORDS]
    if content_terms:
        terms = content_terms
    anchors = _anchors(terms, regex)[:6]
    source_tokens = _source_shaped_tokens(remaining)
    (
        concept_terms,
        structural_kind,
        discovery_score,
        semantic_score,
        documentation_score,
    ) = _query_signals(remaining, terms, source_tokens, regex)
    if not terms and not regex and not scopes:
        warnings.append("query has no searchable terms")
    if len(original) > 256:
        warnings.append(
            "GitHub query exceeds its useful query length; provider queries are shortened"
        )
    return Plan(
        original=original,
        terms=terms,
        anchors=anchors,
        regex=regex,
        repositories=scopes,
        language=language or (parsed.get("language") or [None])[0],
        path=path or (parsed.get("path") or [None])[0],
        filename=filename or (parsed.get("filename") or [None])[0],
        extension=extension or (parsed.get("extension") or [None])[0],
        include_paths=list(include_paths or []),
        exclude_paths=list(exclude_paths or []),
        ref=ref or (parsed.get("ref") or [None])[0],
        case_sensitive=case_sensitive,
        whole_word=whole_word,
        source_tokens=source_tokens,
        concept_terms=concept_terms,
        structural_kind=structural_kind,
        discovery_score=discovery_score,
        semantic_score=semantic_score,
        documentation_score=documentation_score,
        warnings=warnings,
    )


def github_query(plan: Plan, terms: list[str] | None = None) -> str:
    parts = list(terms or plan.terms)
    parts.extend(f"repo:{repo}" for repo in plan.repositories)
    if plan.language:
        parts.append(f"language:{plan.language}")
    if plan.path:
        parts.append(f"path:{plan.path}")
    if plan.filename:
        parts.append(f"filename:{plan.filename}")
    if plan.extension:
        parts.append(f"extension:{plan.extension}")
    return " ".join(parts)[:256].rstrip()


def _quote_sourcegraph_term(term: str) -> str:
    return '"' + term.strip('"').replace('"', '\\"') + '"'


def sourcegraph_query(plan: Plan, *, repository: str | None = None, discovery: bool = False) -> str:
    """Compile native Sourcegraph syntax, not GitHub qualifiers."""
    parts = ["type:file"]
    if discovery:
        parts.append("select:repo")
    if plan.regex:
        sourcegraph_regex = re.sub(r"(?<!\\)/", r"\/", plan.regex)
        parts.append(f"/{sourcegraph_regex}/")
    elif plan.channel == "symbol" and plan.terms:
        symbol = _quote_sourcegraph_term(plan.terms[0])
        parts.append(f"sym:{symbol}")
    elif len(plan.terms) == 1:
        parts.append(f"content:{_quote_sourcegraph_term(plan.terms[0])}")
    else:
        parts.extend(f"content:{_quote_sourcegraph_term(term)}" for term in plan.terms[:8])
    repositories = [normalize_repo(repository)] if repository else plan.repositories
    if repositories:
        escaped_repositories = "|".join(re.escape(f"github.com/{repo}") for repo in repositories)
        repo_expression = (
            escaped_repositories if len(repositories) == 1 else f"({escaped_repositories})"
        )
        parts.append(f"repo:^{repo_expression}$")
    if plan.path:
        parts.append(f"file:{re.escape(plan.path)}")
    if plan.filename:
        parts.append(f"file:(^|/){re.escape(plan.filename)}$")
    if plan.extension:
        ext = plan.extension.removeprefix(".")
        parts.append(f"file:\\.{re.escape(ext)}$")
    if plan.language:
        parts.append(f"lang:{plan.language}")
    if plan.ref:
        parts.append(f"rev:{re.escape(plan.ref)}")
    if plan.case_sensitive:
        parts.append("case:yes")
    for pattern in plan.include_paths:
        parts.append(f"file:{pattern}")
    for pattern in plan.exclude_paths:
        parts.append(f"-file:{pattern}")
    return " ".join(parts)


def _is_document_path(path: str | None) -> bool:
    if not path:
        return False
    name = path.rsplit("/", 1)[-1].casefold().split(".", 1)[0]
    return name in DOC_NAMES or path.casefold().endswith((".md", ".rst", ".txt"))


def _is_noise_path(path: str | None) -> bool:
    if not path:
        return False
    normalized = f"/{path.casefold().strip('/')}/"
    filename = path.rsplit("/", 1)[-1].casefold()
    return (
        any(part in normalized for part in NOISE_PATH_PARTS)
        or "/generated/" in normalized
        or path.casefold().endswith((".min.js", ".min.css", ".map"))
        or filename in NOISE_FILE_NAMES
    )


def _classify_hit(hit: Hit) -> str:
    text = str(hit.source_metadata.get("matched_line") or hit.snippet)
    stripped = text.strip()
    if _is_document_path(hit.path):
        return "documentation"
    if hit.source_metadata.get("matched_line_is_docstring"):
        return "documentation"
    if hit.source_metadata.get("matched_line_is_comment"):
        return "comment"
    if re.search(
        r"(^|/)(test|tests|spec|specs)(/|$)|(^|[._-])(test|spec)([._-]|$)", hit.path or "", re.I
    ):
        return "test"
    if stripped.startswith(("#", "//", "/*", "*", "<!--")):
        return "comment"
    if hit.path:
        suffix = "." + hit.path.rsplit("/", 1)[-1].rsplit(".", 1)[-1].casefold()
        if suffix not in CODE_SUFFIXES:
            if suffix in {".json", ".yaml", ".yml", ".toml", ".ini", ".env"}:
                return "config_key"
            return "code"
    if re.search(r"\b(class|def|function|func|export|async\s+def|interface|struct)\b", text):
        return "declaration"
    if re.search(r"\b(import|from|require|include|use)\b", text):
        return "import"
    if re.search(r"\b\w+\s*\(", text) and re.search(
        r"[;={},]|=>|\b(?:await|return|new|const|let|var|if|for|while|catch|throw)\b",
        text,
    ):
        return "callsite"
    return "code"


def _score_hit(hit: Hit, plan: Plan, provider_rank: int) -> None:
    text = f"{hit.path or ''} {hit.snippet}".casefold()
    score = 0.0
    covered = 0
    for term in plan.anchors[:6]:
        if term.casefold() in text:
            covered += 1
            score += 1.6 if hit.path and term.casefold() in hit.path.casefold() else 1.0
    if plan.anchors:
        score += covered / len(plan.anchors) * 2.0
    if hit.verified:
        score += 3.0
        hit.reasons.append("source verified")
    if "repository discovery proof" in hit.reasons:
        score += 2.5
    if hit.line_start is not None:
        score += 0.8
        hit.reasons.append("line evidence")
    if hit.symbols:
        score += 1.0
        hit.reasons.append("symbol evidence")
    hit.match_kind = _classify_hit(hit)
    score += {
        "declaration": 1.3,
        "export": 1.2,
        "callsite": 0.8,
        "import": 0.2,
        "code": 0.5,
        "comment": -1.5,
        "test": -0.8,
        "config_key": -0.4,
    }.get(hit.match_kind, -1.5)
    if hit.match_kind == "documentation":
        score -= 2.5
        hit.reasons.append("documentation path down-ranked")
    if _is_noise_path(hit.path):
        score -= 2.0
        hit.reasons.append("vendored or generated path down-ranked")
    path_lower = (hit.path or "").casefold()
    if re.search(
        r"(^|/)(test|tests|spec|specs|benchmark|benchmarks)(/|$)|(^|[._-])(test|spec)([._-]|$)",
        path_lower,
    ):
        score -= 1.2
        hit.reasons.append("test or benchmark path down-ranked")
    if re.search(r"(^|/)(config|configs|fixtures|examples?)(/|$)", path_lower):
        score -= 0.6
        hit.reasons.append("supporting path down-ranked")
    if len(plan.anchors) > 1 and hit.snippet:
        snippet_lines = hit.snippet.casefold().splitlines()
        positions = [
            index
            for index, line in enumerate(snippet_lines)
            if any(anchor.casefold() in line for anchor in plan.anchors)
        ]
        if positions:
            proximity = max(0.0, 1.5 - (max(positions) - min(positions)) * 0.15)
            score += proximity
            if proximity >= 1.0:
                hit.reasons.append("query terms occur near each other")
        normalized_query = re.sub(r"[^a-z0-9]+", " ", plan.original.casefold()).strip()
        normalized_line = re.sub(
            r"[^a-z0-9]+", " ", str(hit.source_metadata.get("matched_line", "")).casefold()
        ).strip()
        if normalized_query and normalized_query in normalized_line:
            score += 1.5 if hit.match_kind not in {"documentation", "comment"} else 0.2
            hit.reasons.append("query phrase appears in source evidence")
    score += max(0.0, 1.0 - provider_rank / 100.0)
    hit.score = round(score, 4)


class SearchClient:
    def __init__(self, token: str | None):
        headers = {"User-Agent": UA}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = httpx.AsyncClient(timeout=25, headers=headers)
        self.token = token

    async def close(self) -> None:
        await self.http.aclose()

    async def github_code(self, plan: Plan, limit: int) -> tuple[list[Hit], dict[str, Any]]:
        if plan.ref:
            return [], {
                "provider": "github",
                "status": "skipped; ref search is compiled for Sourcegraph and GitHub hydration",
            }
        if not self.token:
            return [], {"provider": "github", "status": "missing GitHub token"}
        query = github_query(plan)
        response = await self.http.get(
            f"{REST}/search/code",
            params={"q": query, "per_page": min(limit, 100)},
            headers={"Accept": "application/vnd.github.text-match+json"},
        )
        if response.status_code != 200:
            return [], {
                "provider": "github",
                "status": f"http {response.status_code}",
                "query": query,
            }
        data = response.json()
        hits: list[Hit] = []
        for rank, item in enumerate(data.get("items", []), 1):
            repo = (item.get("repository") or {}).get("full_name")
            fragments = item.get("text_matches") or []
            snippet = "\n".join(str(fragment.get("fragment", "")) for fragment in fragments[:2])
            hits.append(
                Hit(
                    repository=repo,
                    path=item.get("path"),
                    url=item.get("html_url", ""),
                    provider="github",
                    snippet=snippet,
                    blob_sha=item.get("sha"),
                    provider_query=query,
                    source_metadata={"search_rank": rank},
                )
            )
        return hits, {
            "provider": "github",
            "query": query,
            "total": data.get("total_count", 0),
            "incomplete": data.get("incomplete_results", False),
        }

    async def sourcegraph(
        self, plan: Plan, limit: int, repository: str | None = None
    ) -> tuple[list[Hit], dict[str, Any]]:
        query = sourcegraph_query(plan, repository=repository)
        pattern_type = "regexp" if plan.regex else "literal"
        document = {
            "query": """
            query($q: String!) {
              search(query: $q, version: V2, patternType: PATTERN) {
                results {
                  matchCount
                  limitHit
                  results {
                    __typename
                    ... on FileMatch {
                      repository { name }
                      file { path url }
                      lineMatches { preview lineNumber offsetAndLengths }
                      symbols { name kind containerName url }
                    }
                  }
                }
              }
            }
            """.replace("PATTERN", pattern_type),
            "variables": {"q": query},
        }
        response = await self.http.post(SOURCEGRAPH, json=document, headers={"User-Agent": UA})
        if response.status_code != 200:
            return [], {
                "provider": "sourcegraph",
                "status": f"http {response.status_code}",
                "query": query,
            }
        data = response.json()
        if data.get("errors"):
            return [], {
                "provider": "sourcegraph",
                "status": data["errors"][0].get("message", "graphql error"),
                "query": query,
            }
        result = ((data.get("data") or {}).get("search") or {}).get("results") or {}
        hits: list[Hit] = []
        for rank, item in enumerate(result.get("results") or [], 1):
            if not item or item.get("__typename") != "FileMatch":
                continue
            repo = ((item.get("repository") or {}).get("name") or "").removeprefix("github.com/")
            file = item.get("file") or {}
            lines = item.get("lineMatches") or []
            match_lines = [
                line.get("lineNumber") + 1 for line in lines if line.get("lineNumber") is not None
            ]
            first_line = min(match_lines) if match_lines else None
            last_line = max(match_lines) if match_lines else None
            snippet = "\n".join(str(line.get("preview", ""))[:240] for line in lines[:4])
            symbols = item.get("symbols") or []
            match_spans = [
                {"line": line_number, "offset": offset, "length": length}
                for line_number, line in zip(match_lines, lines)
                for offset, length in (line.get("offsetAndLengths") or [])
            ]
            file_url = file.get("url") or ""
            if file_url.startswith("/"):
                file_url = f"https://sourcegraph.com{file_url}"
            hit = Hit(
                repository=repo,
                path=file.get("path"),
                url=file_url or f"https://github.com/{repo}/blob/HEAD/{file.get('path', '')}",
                provider="sourcegraph",
                snippet=snippet,
                line_start=first_line,
                line_end=last_line,
                match_line=first_line,
                provider_query=query,
                match_lines=match_lines,
                match_spans=match_spans,
                symbols=symbols[:5],
                source_metadata={
                    "search_rank": rank,
                    "pattern_type": pattern_type,
                },
            )
            if symbols:
                hit.reasons.append("Sourcegraph symbol evidence")
            hits.append(hit)
        return hits, {
            "provider": "sourcegraph",
            "query": query,
            "match_count": result.get("matchCount", 0),
            "limit_hit": result.get("limitHit", False),
        }

    async def grepapp(self, plan: Plan, limit: int) -> tuple[list[Hit], dict[str, Any]]:
        if plan.ref:
            return [], {
                "provider": "grep.app",
                "status": "skipped; grep.app does not expose revision-scoped search",
            }
        if len(plan.repositories) > 1:
            return [], {
                "provider": "grep.app",
                "status": "skipped; direct grep.app adapter accepts one repository scope",
                "query": plan.regex or " ".join(plan.terms),
            }
        query = plan.regex or " ".join(plan.terms)
        params: dict[str, Any] = {"q": query, "regexp": "true" if plan.regex else "false"}
        if plan.language:
            params["f.lang"] = plan.language
        if plan.repositories:
            params["f.repo"] = plan.repositories[0]
        if plan.path:
            params["f.path"] = plan.path
        response = await self.http.get(GREPAPP, params=params)
        if response.status_code in (403, 429):
            return [], {
                "provider": "grep.app",
                "status": f"http {response.status_code}; MCP transport required",
                "query": query,
            }
        if response.status_code != 200:
            return [], {
                "provider": "grep.app",
                "status": f"http {response.status_code}",
                "query": query,
            }
        try:
            data = response.json()
        except ValueError:
            return [], {"provider": "grep.app", "status": "non-JSON response", "query": query}
        hits: list[Hit] = []
        for rank, item in enumerate((((data.get("hits") or {}).get("hits")) or [])[:limit], 1):
            content = item.get("content") or {}
            hits.append(
                Hit(
                    repository=(item.get("repo") or {}).get("raw"),
                    path=(item.get("path") or {}).get("raw"),
                    url=f"https://github.com/{(item.get('repo') or {}).get('raw', '')}/blob/{(item.get('branch') or {}).get('raw', 'HEAD')}/{(item.get('path') or {}).get('raw', '')}",
                    provider="grep.app",
                    snippet=str(content.get("snippet", ""))[:500],
                    provider_query=query,
                    source_metadata={"search_rank": rank},
                )
            )
        return hits, {
            "provider": "grep.app",
            "query": query,
            "total": ((data.get("hits") or {}).get("total", 0)),
        }

    async def discover_repositories(
        self, plan: Plan, limit: int, options: Any = None
    ) -> tuple[list[RepoCandidate], dict[str, Any]]:
        if not self.token:
            return [], {"provider": "github-repositories", "status": "missing GitHub token"}
        search_terms = " ".join(plan.anchors[:4] or plan.terms[:4])
        query = search_terms
        if options:
            query_parts = [query]
            query_parts.extend(f"topic:{topic}" for topic in options.topics)
            query_parts.extend(f"org:{owner}" for owner in options.owners)
            if options.language:
                query_parts.append(f"language:{options.language}")
            if options.min_stars is not None:
                query_parts.append(f"stars:>={options.min_stars}")
            if options.sort != "best":
                query_parts.append(f"sort:{options.sort}")
            query = " ".join(part for part in query_parts if part)
        gql = """
        query($q: String!, $n: Int!) {
          search(query: $q, type: REPOSITORY, first: $n) {
            repositoryCount
            nodes { ... on Repository {
              nameWithOwner url description stargazerCount pushedAt
              primaryLanguage { name }
              repositoryTopics(first: 10) { nodes { topic { name } } }
              object(expression: "HEAD:README.md") { ... on Blob { text } }
            } }
          }
        }
        """
        response = await self.http.post(
            GRAPHQL, json={"query": gql, "variables": {"q": query, "n": min(limit, 20)}}
        )
        if response.status_code != 200:
            return [], {
                "provider": "github-repositories",
                "status": f"http {response.status_code}",
                "query": query,
            }
        data = response.json()
        nodes = (((data.get("data") or {}).get("search") or {}).get("nodes")) or []
        candidates: list[RepoCandidate] = []
        terms = [x.casefold() for x in plan.anchors or plan.terms]
        for node in nodes:
            if not node:
                continue
            topics = [
                ((x.get("topic") or {}).get("name") or "")
                for x in ((node.get("repositoryTopics") or {}).get("nodes") or [])
            ]
            description = node.get("description") or ""
            readme = re.sub(r"\s+", " ", (node.get("object") or {}).get("text") or "").strip()
            readme = re.sub(r"!\[[^]]*\]\([^)]*\)|\[[^]]*\]\([^)]*\)", "", readme)
            corpus = " ".join(
                [node.get("nameWithOwner", ""), description, readme, *topics]
            ).casefold()
            coverage = sum(1 for term in terms if term in corpus)
            relevance = coverage * 2.0 + math.log1p(node.get("stargazerCount", 0)) * 0.05
            candidates.append(
                RepoCandidate(
                    repository=node.get("nameWithOwner", ""),
                    url=node.get("url", ""),
                    description=description[:300],
                    readme_preview=readme[:500],
                    topics=topics,
                    language=((node.get("primaryLanguage") or {}).get("name")),
                    stars=node.get("stargazerCount", 0),
                    pushed_at=node.get("pushedAt"),
                    relevance=relevance,
                )
            )
        candidates.sort(key=lambda item: (-item.relevance, -item.stars))
        return candidates, {
            "provider": "github-repositories",
            "query": query,
            "total": ((data.get("data") or {}).get("search") or {}).get("repositoryCount", 0),
        }


def _line_matches_kind(line: str, kind: str | list[str] | None) -> bool:
    if not kind:
        return True
    if isinstance(kind, list):
        return any(_line_matches_kind(line, item) for item in kind)
    if kind in {
        "implementation",
        "identifier",
        "code",
        "test",
        "config_key",
        "comment",
        "string",
        "unknown",
    }:
        return True
    if kind == "declaration":
        return bool(re.search(r"\b(class|def|function|func|export|interface|struct|enum)\b", line))
    if kind == "import":
        return bool(re.search(r"\b(import|from|require|include|use)\b", line))
    if kind == "callsite":
        return bool(
            re.search(r"\b\w+\s*\(", line)
            and re.search(
                r"[;={},]|=>|\b(?:await|return|new|const|let|var|if|for|while|catch|throw)\b",
                line,
            )
        )
    return True


def _role_matches(actual: str, requested: list[str]) -> bool:
    if not requested:
        return True
    return any(actual in ROLE_EXPANSIONS.get(role, {role}) for role in requested)


def _is_python_docstring(source: str, path: str | None, line: int) -> bool:
    if not path or not path.casefold().endswith(".py"):
        return False
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.STRING and token.start[0] <= line <= token.end[0]:
                return True
    except (IndentationError, SyntaxError, tokenize.TokenError):
        return False
    return False


async def hydrate_github(
    client: SearchClient,
    hits: list[Hit],
    plan: Plan,
    kind: str | list[str] | None = None,
    context_lines: int = 6,
) -> None:
    """Fetch current source for GitHub hits and mark actual code evidence."""

    async def one(hit: Hit) -> None:
        if (
            hit.verified
            or hit.provider not in {"github", "sourcegraph"}
            or not hit.repository
            or not hit.path
            or _is_document_path(hit.path)
        ):
            return
        repository_parts = hit.repository.split("/", 1)
        if len(repository_parts) != 2:
            hit.source_metadata["hydration_error"] = "repository is not owner/repo"
            return
        owner, repo = repository_parts
        commit_params: dict[str, Any] = {"path": hit.path, "per_page": 1}
        if plan.ref:
            commit_params["sha"] = plan.ref
        commits = await client.http.get(
            f"{REST}/repos/{owner}/{repo}/commits", params=commit_params
        )
        if commits.status_code != 200 or not commits.json():
            return
        revision = commits.json()[0].get("sha")
        if not revision:
            return
        raw = await client.http.get(
            f"https://raw.githubusercontent.com/{owner}/{repo}/{revision}/{hit.path}"
        )
        if raw.status_code != 200:
            return
        source = raw.text
        verification_terms = hit.source_metadata.get("verification_terms")
        verification_regex = hit.source_metadata.get("verification_regex")
        hit_plan = plan
        if isinstance(verification_terms, list):
            hit_plan = replace(
                plan,
                terms=[str(term) for term in verification_terms],
                anchors=_anchors([str(term) for term in verification_terms], verification_regex),
                regex=verification_regex,
            )
        lines = source.splitlines()
        line_offsets: list[int] = []
        cursor = 0
        for raw_line in source.splitlines(keepends=True):
            line_offsets.append(cursor)
            cursor += len(raw_line)
        preferred_lines = hit.match_lines or ([hit.match_line] if hit.match_line else [])

        def query_patterns() -> list[str]:
            patterns: list[str] = []
            for term in hit_plan.terms:
                clean = term.strip("\"'")
                if len(clean) < 3:
                    continue
                if hit_plan.whole_word and re.fullmatch(r"[A-Za-z0-9_$]+", clean):
                    patterns.append(rf"(?<![A-Za-z0-9_$]){re.escape(clean)}(?![A-Za-z0-9_$])")
                else:
                    patterns.append(re.escape(clean))
            return patterns

        patterns = query_patterns()
        match_flags = 0 if hit_plan.case_sensitive else re.IGNORECASE
        if kind and patterns and not hit_plan.regex and len(patterns) == 1:
            for index, source_line in enumerate(lines):
                if _line_matches_kind(source_line, kind) and all(
                    re.search(pattern, source_line, flags=match_flags) for pattern in patterns
                ):
                    first_match = re.search(patterns[0], source_line, flags=match_flags)
                    match_start = line_offsets[index] + (first_match.start() if first_match else 0)
                    break
            else:
                match_start = None
            if match_start is None:
                return
        else:
            match_start = None

        if len(patterns) > 1 and not hit_plan.regex:
            occurrences: list[tuple[int, int, int]] = []
            for pattern_index, pattern in enumerate(patterns):
                for line_index, source_line in enumerate(lines):
                    found = re.search(pattern, source_line, flags=match_flags)
                    if found:
                        occurrences.append((line_index, pattern_index, found.start()))
            occurrences.sort()
            counts: dict[int, int] = {}
            left = 0
            best: tuple[int, int, int, int] | None = None
            for right, (right_line, right_pattern, _) in enumerate(occurrences):
                counts[right_pattern] = counts.get(right_pattern, 0) + 1
                while len(counts) == len(patterns) and left <= right:
                    left_line = occurrences[left][0]
                    span = right_line - left_line
                    center = (left_line + right_line) // 2 + 1
                    hint = hit.match_line or hit.line_start or center
                    candidate = (span, abs(center - hint), left, right)
                    if best is None or candidate[:2] < best[:2]:
                        best = candidate
                    left_pattern = occurrences[left][1]
                    counts[left_pattern] -= 1
                    if counts[left_pattern] == 0:
                        del counts[left_pattern]
                    left += 1
            max_locality = max(12, context_lines * 2 + 2)
            if best is None or best[0] > max_locality:
                return
            window_start = occurrences[best[2]][0]
            window_end = occurrences[best[3]][0]
            preferred_line = min(
                range(window_start, window_end + 1),
                key=lambda index: abs(
                    (index + 1) - (hit.match_line or hit.line_start or index + 1)
                ),
            )
            if kind and not _line_matches_kind(lines[preferred_line], kind):
                eligible = [
                    index
                    for index in range(window_start, window_end + 1)
                    if _line_matches_kind(lines[index], kind)
                ]
                if not eligible:
                    return
                preferred_line = eligible[0]
            first_pattern = re.search(patterns[0], lines[preferred_line], flags=match_flags)
            match_start = line_offsets[preferred_line] + (
                first_pattern.start() if first_pattern else 0
            )
        elif match_start is None and kind and patterns and not hit_plan.regex:
            return

        def find_position(pattern: str, line_numbers: list[int]) -> int | None:
            for line_number in line_numbers:
                if not 1 <= line_number <= len(lines):
                    continue
                found = re.search(pattern, lines[line_number - 1], flags=match_flags)
                if found:
                    return line_offsets[line_number - 1] + found.start()
            found = re.search(pattern, source, re.MULTILINE | match_flags)
            return found.start() if found else None

        if match_start is not None:
            pass
        elif hit_plan.regex:
            try:
                match_start = find_position(hit_plan.regex, preferred_lines)
            except re.error:
                match_start = None
        else:
            term_patterns: list[str] = []
            global_positions: list[int] = []
            for term in hit_plan.terms:
                clean = term.strip("\"'")
                if len(clean) < 3:
                    continue
                if hit_plan.whole_word and re.fullmatch(r"[A-Za-z0-9_$]+", clean):
                    pattern = rf"(?<![A-Za-z0-9_$]){re.escape(clean)}(?![A-Za-z0-9_$])"
                else:
                    pattern = re.escape(clean)
                position = find_position(pattern, [])
                if position is None:
                    term_patterns = []
                    global_positions = []
                    break
                term_patterns.append(pattern)
                global_positions.append(position)
            if term_patterns:
                preferred_positions = [
                    position
                    for pattern in term_patterns
                    if (position := find_position(pattern, preferred_lines)) is not None
                ]
                if preferred_positions:
                    match_start = preferred_positions[0]
                else:
                    hint = hit.match_line or hit.line_start or 0
                    match_start = min(
                        global_positions,
                        key=lambda position: abs(source[:position].count("\n") + 1 - hint),
                    )
            else:
                match_start = None
        if match_start is None:
            return
        line = source[:match_start].count("\n") + 1
        matched_line = lines[line - 1] if 0 < line <= len(lines) else ""
        start = max(1, line - context_lines)
        end = min(len(lines), line + context_lines)
        hit.revision = revision
        hit.hydrated_commit = revision
        hit.match_line = line
        hit.match_lines = sorted(set(hit.match_lines + [line]))
        hit.url = f"https://github.com/{hit.repository}/blob/{revision}/{hit.path}#L{line}"
        hit.line_start = start
        hit.line_end = end
        hit.snippet = "\n".join(f"{idx}: {lines[idx - 1]}" for idx in range(start, end + 1))[:1800]
        hit.source_metadata["matched_line"] = matched_line
        if not any(span.get("line") == line for span in hit.match_spans):
            hit.match_spans.append({"line": line, "text": matched_line})
        hit.source_metadata["matched_line_is_docstring"] = _is_python_docstring(
            source, hit.path, line
        )
        comment_markers = [
            position for marker in ("#", "//", "/*") if (position := matched_line.find(marker)) >= 0
        ]
        local_match = match_start - line_offsets[line - 1] if line_offsets else 0
        hit.source_metadata["matched_line_is_comment"] = bool(
            comment_markers and local_match >= min(comment_markers)
        )
        hit.verified = True
        hit.reasons.append("matched hydrated source")

    await asyncio.gather(*(one(hit) for hit in hits))


async def run_provider(name: str, operation: Any) -> tuple[list[Hit], dict[str, Any]]:
    """Keep provider failures attributable without hiding them from the caller."""
    try:
        hits, status = await operation
        return hits, status
    except Exception as exc:  # noqa: BLE001 - status is returned to the agent
        return [], {
            "provider": name,
            "status": f"{type(exc).__name__}: {exc}",
        }


async def run_variant_provider(
    name: str, variant_id: str, variant_plan: Plan, operation: Any
) -> tuple[list[Hit], dict[str, Any]]:
    hits, status = await run_provider(name, operation)
    status["variant_id"] = variant_id
    status["returned_count"] = len(hits)
    for hit in hits:
        hit.source_metadata["query_variant"] = variant_id
        hit.source_metadata["verification_terms"] = variant_plan.terms
        hit.source_metadata["verification_regex"] = variant_plan.regex
    return hits, status


async def execute(request: SearchInput) -> dict[str, Any]:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        try:
            token = (
                subprocess.run(
                    ["gh", "auth", "token"], capture_output=True, text=True, check=False, timeout=10
                ).stdout.strip()
                or None
            )
        except Exception:
            token = None
    plan = plan_query(
        request.query,
        repositories=request.repositories,
        language=request.language,
        path=request.path,
        filename=request.filename,
        extension=request.extension,
        regexp=False,
        include_paths=request.include_paths,
        exclude_paths=request.exclude_paths,
        ref=request.scope.ref,
        case_sensitive=request.match.case_sensitive,
        whole_word=request.match.whole_word,
    )
    client = SearchClient(token)
    started = time.perf_counter()
    try:
        discovery: list[RepoCandidate] = []
        discovery_status: dict[str, Any] | None = None
        discovery_requested = request.discover_repositories or plan.discovery_score >= 0.5
        if discovery_requested and not plan.repositories:
            discovery, discovery_status = await client.discover_repositories(
                plan,
                request.discovery.limit if request.discovery else request.result_limit,
                request.discovery,
            )

        operations: list[Any] = []
        statuses: list[dict[str, Any]] = []
        discovery_only = discovery_requested and not plan.repositories
        variants = search_plans(plan)
        if not discovery_only:
            for variant_id, variant_plan in variants:
                if variant_plan.regex:
                    if variant_id == "primary":
                        statuses.append(
                            {
                                "provider": "github",
                                "status": "skipped; GitHub code search has no regex mode",
                                "query": github_query(variant_plan),
                                "variant_id": variant_id,
                            }
                        )
                elif len(variant_plan.repositories) > 1:
                    statuses.append(
                        {
                            "provider": "github",
                            "status": "skipped; GitHub query needs one repository scope",
                            "query": github_query(variant_plan),
                            "variant_id": variant_id,
                        }
                    )
                else:
                    operations.append(
                        run_variant_provider(
                            "github",
                            variant_id,
                            variant_plan,
                            client.github_code(variant_plan, request.result_limit),
                        )
                    )
                operations.extend(
                    [
                        run_variant_provider(
                            "sourcegraph",
                            variant_id,
                            variant_plan,
                            client.sourcegraph(variant_plan, request.result_limit),
                        ),
                        run_variant_provider(
                            "grep.app",
                            variant_id,
                            variant_plan,
                            client.grepapp(variant_plan, request.result_limit),
                        ),
                    ]
                )
        if plan.semantic_score >= 0.5 and os.environ.get("EXA_API_KEY"):
            operations.append(
                client.http.post(
                    EXA_SEARCH,
                    headers={"x-api-key": os.environ["EXA_API_KEY"]},
                    json={
                        "query": request.query,
                        "type": "fast",
                        "numResults": request.result_limit,
                        "includeDomains": ["github.com"],
                        "contents": {
                            "highlights": {"query": request.query},
                            "text": {"maxCharacters": 500},
                        },
                    },
                )
            )

        responses = await asyncio.gather(*operations, return_exceptions=True)
        hits: list[Hit] = []
        for response in responses:
            if isinstance(response, tuple):
                branch_hits, status = response
                hits.extend(branch_hits)
                statuses.append(status)
            elif isinstance(response, httpx.Response):
                if response.status_code == 200:
                    for result in response.json().get("results", []):
                        hits.append(
                            Hit(
                                repository=None,
                                path=None,
                                url=result.get("url", ""),
                                provider="exa",
                                snippet="\n".join(result.get("highlights") or [])[:1000],
                                title=result.get("title", ""),
                            )
                        )
                    statuses.append({"provider": "exa", "status": "ok"})
                else:
                    statuses.append({"provider": "exa", "status": f"http {response.status_code}"})
            else:
                statuses.append({"provider": "unknown", "status": str(response)})

        if discovery_only and (not request.discovery or request.discovery.prove):
            # Prove the best repository candidates with scoped code searches and
            # expose those proof snippets as ordinary results for the agent.
            async def prove(candidate: RepoCandidate) -> tuple[RepoCandidate, list[Hit]]:
                verified: list[Hit] = []
                selected_query: str | None = None
                candidate.proof_requested = True
                for proof_plan in proof_plans(plan):
                    proof, proof_status = await client.sourcegraph(
                        proof_plan, request.result_limit, repository=candidate.repository
                    )
                    proof_status["returned_count"] = len(proof)
                    proof_status["proof_variant"] = sourcegraph_query(
                        proof_plan, repository=candidate.repository
                    )
                    statuses.append(proof_status)
                    await hydrate_github(
                        client, proof, proof_plan, request.roles or None, request.context_lines
                    )
                    for hit in proof:
                        hit.source_metadata["query_variant"] = "proof"
                    verified = [hit for hit in proof if hit.verified]
                    if verified:
                        selected_query = sourcegraph_query(
                            proof_plan, repository=candidate.repository
                        )
                        break
                for hit in verified:
                    hit.reasons.append("repository discovery proof")
                candidate.proof_hits = len(verified)
                candidate.proof_paths = [
                    f"{hit.path}#L{hit.line_start}" if hit.line_start else str(hit.path)
                    for hit in verified
                ]
                candidate.verified = bool(verified)
                candidate.proof_query = selected_query
                return candidate, verified

            proven = await asyncio.gather(
                *(prove(candidate) for candidate in discovery[: request.result_limit])
            )
            for _, proof_hits in proven:
                hits.extend(proof_hits)
            discovery.sort(
                key=lambda item: (
                    not item.verified,
                    -item.proof_hits,
                    -item.relevance,
                    -item.stars,
                )
            )

        await hydrate_github(client, hits, plan, request.roles or None, request.context_lines)
        for rank, hit in enumerate(hits, 1):
            provider_rank = hit.source_metadata.get("search_rank", rank)
            _score_hit(hit, plan, int(provider_rank))
        filtered_hits: list[Hit] = []
        for hit in hits:
            if request.roles and (
                not hit.verified or not _role_matches(hit.match_kind, request.roles)
            ):
                continue
            if request.include_paths and not any(
                re.search(pattern, hit.path or "", flags=re.IGNORECASE)
                for pattern in request.include_paths
            ):
                continue
            if any(
                re.search(pattern, hit.path or "", flags=re.IGNORECASE)
                for pattern in request.exclude_paths
            ):
                hit.reasons.append("excluded by path filter")
                continue
            filtered_hits.append(hit)
        unique: dict[tuple[str | None, str | None], Hit] = {}
        for hit in sorted(filtered_hits, key=lambda item: -item.score):
            if not hit.providers:
                hit.providers = [hit.provider]
            if hit.provider_query and not hit.provider_queries:
                hit.provider_queries = [hit.provider_query]
            key = (hit.repository, hit.path or hit.url)
            if key not in unique:
                unique[key] = hit
            else:
                existing = unique[key]
                existing.providers = sorted(set(existing.providers + [hit.provider]))
                if hit.provider_query:
                    existing.provider_queries = sorted(
                        set(existing.provider_queries + [hit.provider_query])
                    )
                existing.match_lines = sorted(set(existing.match_lines + hit.match_lines))
                existing.match_spans.extend(
                    span for span in hit.match_spans if span not in existing.match_spans
                )
                existing.symbols.extend(
                    symbol for symbol in hit.symbols if symbol not in existing.symbols
                )
                existing_variants = existing.source_metadata.setdefault("query_variants", [])
                current_variant = existing.source_metadata.get("query_variant")
                if current_variant and current_variant not in existing_variants:
                    existing_variants.append(current_variant)
                for variant in hit.source_metadata.get("query_variants", []) or (
                    [hit.source_metadata["query_variant"]]
                    if hit.source_metadata.get("query_variant")
                    else []
                ):
                    if variant not in existing_variants:
                        existing_variants.append(variant)
                existing.score = round(existing.score + 0.75, 4)
                existing.reasons.append(f"provider agreement: {hit.provider}")

        query_variants = [
            {
                "id": variant_id,
                "kind": (
                    "regex"
                    if variant_plan.regex
                    else (
                        "primary"
                        if variant_id == "primary"
                        else ("compact" if variant_id == "compact" else "identifier")
                    )
                ),
                "provider": provider,
                "query": provider_query,
                "filters": {
                    key: value
                    for key, value in {
                        "repositories": variant_plan.repositories,
                        "language": variant_plan.language,
                        "path": variant_plan.path,
                        "filename": variant_plan.filename,
                        "extension": variant_plan.extension,
                        "include": variant_plan.include_paths,
                        "exclude": variant_plan.exclude_paths,
                        "ref": variant_plan.ref,
                    }.items()
                    if value
                },
            }
            for variant_id, variant_plan in variants
            for provider, provider_query in (
                ("github", github_query(variant_plan)),
                ("sourcegraph", sourcegraph_query(variant_plan)),
                ("grep.app", variant_plan.regex or " ".join(variant_plan.terms)),
            )
        ]
        if discovery_only:
            query_variants.append(
                {
                    "id": "proof",
                    "kind": "proof",
                    "provider": "sourcegraph",
                    "query": sourcegraph_query(plan),
                }
            )
        return {
            "query": request.query,
            "query_interpretation": plan.query_interpretation,
            "variants": query_variants,
            "matches": [
                asdict(hit)
                for hit in sorted(unique.values(), key=lambda item: -item.score)[
                    : request.result_limit
                ]
            ],
            "repositories": [asdict(candidate) for candidate in discovery],
            "providers": statuses + ([discovery_status] if discovery_status else []),
            "warnings": plan.warnings,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Disposable refined code-search prototype")
    parser.add_argument("query", nargs="?")
    parser.add_argument("--repo", action="append", default=[])
    parser.add_argument("--language", action="append", default=[])
    parser.add_argument("--path")
    parser.add_argument("--filename")
    parser.add_argument("--extension")
    parser.add_argument("--ref")
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--case-sensitive", action="store_true")
    parser.add_argument("--whole-word", action="store_true")
    parser.add_argument(
        "--kind",
        choices=[
            "declaration",
            "export",
            "implementation",
            "callsite",
            "import",
            "identifier",
            "test",
            "config_key",
            "comment",
            "string",
            "code",
            "documentation",
            "generated",
            "unknown",
        ],
        help="return only this evidence role after hydration",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="case-insensitive path regex to exclude; repeatable",
    )
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument(
        "--context", type=int, default=6, help="source lines on each side of a match"
    )
    parser.add_argument(
        "--schema", action="store_true", help="print the validated input/output JSON Schemas"
    )
    args = parser.parse_args()
    try:
        if args.schema:
            print(
                json.dumps(
                    {
                        "input": SearchInput.model_json_schema(),
                        "output": SearchResponse.model_json_schema(),
                    },
                    indent=2,
                )
            )
            return 0
        if not args.query:
            parser.error("query is required unless --schema is used")
        request = SearchInput(
            query=args.query,
            scope={
                "repositories": args.repo,
                "languages": args.language,
                "path": args.path,
                "filenames": [args.filename] if args.filename else [],
                "extensions": [args.extension] if args.extension else [],
                "include": args.include,
                "exclude": args.exclude,
                "ref": args.ref,
            },
            match={
                "case_sensitive": args.case_sensitive,
                "whole_word": args.whole_word,
            },
            roles=[args.kind] if args.kind else [],
            limit=args.top,
            context_lines=args.context,
        )
        response = SearchResponse.model_validate(asyncio.run(execute(request)))
        print(response.model_dump_json(indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
