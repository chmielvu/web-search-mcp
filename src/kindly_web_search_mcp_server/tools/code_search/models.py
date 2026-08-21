"""Typed contracts for the multi-provider code-search tool."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from ...evals.metrics import assess_candidate_readiness

Outcome = Literal["ok", "no_hit", "partial", "error", "skipped"]
ResultKind = Literal["code_match", "semantic_page", "documentation", "repository"]
LocationPrecision = Literal["line", "file", "url", "repository", "unknown"]
FailureKind = Literal[
    "auth",
    "rate_limit",
    "validation",
    "not_found",
    "network",
    "provider",
    "incomplete_index",
    "budget",
]


class Diagnostic(BaseModel):
    """Machine-readable provider or budget diagnostic."""

    provider: str = Field(description="Provider or backend that emitted this diagnostic.")
    outcome: Outcome = Field(default="error", description="Outcome of the provider operation.")
    message: str = Field(description="Human-readable explanation of the outcome or failure.")
    failure_kind: FailureKind = Field(
        default="provider", description="Normalized category of the failure, when applicable."
    )
    status_code: int | None = Field(
        default=None, description="HTTP status code returned by the provider, when available."
    )
    retry_after_seconds: float | None = Field(
        default=None, description="Suggested delay before retrying, when supplied by the provider."
    )
    query: str | None = Field(
        default=None, description="Compiled query variant associated with the diagnostic."
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured provider details retained for diagnosis and follow-up.",
    )


class RepoCandidate(BaseModel):
    """Repository metadata used for discovery, scoring, and hydration."""

    name_with_owner: str = Field(
        description="Canonical GitHub repository identifier, such as prefecthq/fastmcp."
    )
    url: str | None = Field(default=None, description="Canonical repository URL.")
    description: str | None = Field(default=None, description="Repository description from GitHub.")
    stars: int = Field(default=0, description="GitHub stargazer count at discovery time.")
    forks: int = Field(default=0, description="GitHub fork count at discovery time.")
    pushed_at: str | None = Field(
        default=None, description="Timestamp of the latest repository push, when available."
    )
    language: str | None = Field(default=None, description="Primary language reported by GitHub.")
    topics: list[str] = Field(
        default_factory=list, description="GitHub topics associated with the repository."
    )
    license_spdx_id: str | None = Field(
        default=None, description="SPDX license identifier reported by GitHub, when available."
    )
    homepage_url: str | None = Field(
        default=None, description="Repository homepage URL, when available."
    )
    default_branch: str | None = Field(
        default=None, description="Default branch used for follow-up source inspection."
    )
    head_oid: str | None = Field(
        default=None, description="Exact default-branch commit OID captured during discovery."
    )
    archived: bool = Field(
        default=False, description="Whether GitHub marks the repository as archived."
    )
    fork: bool = Field(default=False, description="Whether GitHub marks the repository as a fork.")
    discovery_rank: int | None = Field(
        default=None, description="Rank returned by repository discovery before local reranking."
    )
    discovery_score: float = Field(
        default=0.0, description="Local relevance score for the repository candidate."
    )
    discovery_queries: list[str] = Field(
        default_factory=list, description="Repository-search queries that produced this candidate."
    )
    proof_hits: int = Field(
        default=0, description="Number of code-search hits supporting this repository candidate."
    )
    proof_paths: list[str] = Field(
        default_factory=list, description="Paths that provide code evidence for the candidate."
    )
    proof_providers: list[str] = Field(
        default_factory=list, description="Providers that supplied supporting code evidence."
    )
    verified: bool = Field(
        default=False,
        description="Whether this candidate has at least one supporting code-search hit in this response.",
    )


class LocationMetadata(BaseModel):
    """Caller-facing precision metadata for an evidence location."""

    precision: LocationPrecision = Field(
        default="unknown",
        description="Strongest location precision supported by this evidence.",
    )
    url: str | None = Field(default=None, description="Canonical evidence URL, when available.")
    path: str | None = Field(default=None, description="Repository-relative path, when available.")
    line_start: int | None = Field(
        default=None, description="One-based first exact source line, when available."
    )
    line_end: int | None = Field(
        default=None, description="One-based last exact source line, when available."
    )
    revision: str | None = Field(
        default=None,
        description="Immutable source revision/commit, never a branch name.",
    )
    ref: str | None = Field(
        default=None,
        description="Branch or tag reference when known but not an immutable revision.",
    )
    lines_available: bool = Field(
        default=False, description="Whether exact line coordinates are available."
    )
    revision_available: bool = Field(
        default=False, description="Whether an immutable source revision is available."
    )
    match_data_available: bool = Field(
        default=False,
        description="Whether the provider supplied exact match data rather than semantic context.",
    )


class TextFragment(BaseModel):
    """A bounded source/search fragment with optional line metadata."""

    text: str = Field(default="", description="Source or search text for this bounded fragment.")
    line_start: int | None = Field(
        default=None, description="One-based first source line represented by the fragment."
    )
    line_end: int | None = Field(
        default=None, description="One-based last source line represented by the fragment."
    )
    match_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific match offsets or classification metadata.",
    )


class CodeSearchHit(BaseModel):
    """Provider-neutral evidence item; ``location`` is the canonical precision metadata."""

    result_kind: ResultKind = Field(
        default="code_match",
        description="Evidence kind: exact code match, semantic page, documentation, or repository.",
    )
    location: LocationMetadata = Field(
        default_factory=LocationMetadata,
        description="Explicit location precision and availability metadata.",
    )

    repository: str | None = Field(
        default=None, description="Repository containing the evidence, when identified."
    )
    path: str | None = Field(default=None, description="Repository-relative file path.")
    sha: str | None = Field(
        default=None, description="Provider blob SHA or file revision identifier, when available."
    )
    url: str = Field(default="", description="Canonical URL for the evidence or source file.")
    provider: str = Field(description="Backend that returned this evidence item.")
    query_variant: str | None = Field(
        default=None,
        description=(
            "Planner or provider query variant associated with this hit. Provider-specific "
            "compiled queries are retained in query_metadata.compiled_queries."
        ),
    )
    search_rank: int | None = Field(
        default=None, description="Provider-native rank before cross-provider ranking."
    )
    fragments: list[TextFragment] = Field(
        default_factory=list,
        description="Bounded source or provider fragments containing the match.",
    )
    commit_oid: str | None = Field(
        default=None, description="Exact indexed or hydrated commit OID, when available."
    )
    hydrated_source: str | None = Field(
        default=None, description="Bounded source window fetched for follow-up analysis."
    )
    hydrated_source_truncated: bool = Field(
        default=False, description="Whether the hydrated source window was truncated."
    )
    line_start: int | None = Field(
        default=None,
        description="Top-level mirror of location.line_start for compatibility, when known.",
    )
    line_end: int | None = Field(
        default=None,
        description="Top-level mirror of location.line_end for compatibility, when known.",
    )
    match_spans: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Character or line spans identified as matching the query.",
    )
    symbols: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Structured symbols associated with the matched source.",
    )
    evidence_role: str | None = Field(
        default=None,
        description="Role of this evidence, such as implementation, documentation, or repository proof.",
    )
    title: str | None = Field(
        default=None, description="Human-readable title supplied by the provider."
    )
    snippet: str | None = Field(
        default=None, description="Short provider snippet suitable for triage."
    )
    published_date: str | None = Field(
        default=None, description="Publication or update date, when supplied by the provider."
    )
    score: float | None = Field(
        default=None, description="Final provider-neutral relevance score after ranking."
    )
    score_components: dict[str, Any] = Field(
        default_factory=dict,
        description="Explainable components contributing to the final score.",
    )
    reasons: list[str] = Field(
        default_factory=list,
        description="Short ranking reasons explaining why this hit was selected.",
    )
    source_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider and hydration metadata for continued investigation.",
    )


def _normalize_line(value: int | None) -> int | None:
    """Accept only positive one-based source coordinates."""

    return value if isinstance(value, int) and value >= 1 else None


def _normalize_line_range(
    line_start: int | None,
    line_end: int | None,
) -> tuple[int | None, int | None]:
    """Repair incomplete or invalid provider line ranges conservatively."""

    normalized_start = _normalize_line(line_start)
    normalized_end = _normalize_line(line_end)
    if normalized_start is None:
        normalized_start = normalized_end
    if normalized_start is not None and (
        normalized_end is None or normalized_end < normalized_start
    ):
        normalized_end = normalized_start
    return normalized_start, normalized_end


def build_location_metadata(
    *,
    repository: str | None,
    path: str | None,
    url: str | None,
    line_start: int | None = None,
    line_end: int | None = None,
    revision: str | None = None,
    ref: str | None = None,
    match_data_available: bool = False,
) -> LocationMetadata:
    """Build honest caller-facing location metadata from known coordinates."""

    normalized_start, normalized_end = _normalize_line_range(line_start, line_end)
    lines_available = normalized_start is not None
    if lines_available:
        precision: LocationPrecision = "line"
    elif path:
        precision = "file"
    elif url:
        precision = "url"
    elif repository:
        precision = "repository"
    else:
        precision = "unknown"
    return LocationMetadata(
        precision=precision,
        url=url,
        path=path,
        line_start=normalized_start,
        line_end=normalized_end,
        revision=revision,
        ref=ref,
        lines_available=lines_available,
        revision_available=revision is not None,
        match_data_available=match_data_available,
    )


_PROVIDER_RESULT_KINDS: dict[str, ResultKind] = {
    "exa": "semantic_page",
    "deepwiki": "documentation",
    "context7": "documentation",
}


def normalize_hit_metadata(hit: CodeSearchHit) -> CodeSearchHit:
    """Refresh result kind/location after provider work or hydration."""

    mapped_kind = _PROVIDER_RESULT_KINDS.get(hit.provider.casefold())
    if mapped_kind is not None:
        hit.result_kind = mapped_kind

    provider = hit.provider.casefold()
    match_data_available = hit.location.match_data_available
    if not match_data_available and provider in {"github", "sourcegraph", "grep.app"}:
        match_data_available = bool(hit.fragments or hit.match_spans)

    revision = hit.location.revision or hit.commit_oid
    if provider == "grep.app" and hit.location.ref:
        revision = None

    hit.line_start, hit.line_end = _normalize_line_range(hit.line_start, hit.line_end)
    hit.location = build_location_metadata(
        repository=hit.repository,
        path=hit.path,
        url=hit.url,
        line_start=hit.line_start,
        line_end=hit.line_end,
        revision=revision,
        ref=hit.location.ref,
        match_data_available=match_data_available,
    )
    return hit


class Stats(BaseModel):
    """Bounded execution statistics safe to expose to MCP callers."""

    provider_counts: dict[str, int] = Field(
        default_factory=dict, description="Number of returned hits attributed to each provider."
    )
    request_count: int = Field(
        default=0, description="Number of provider requests issued by the search."
    )
    hydration_count: int = Field(
        default=0, description="Number of source hydration requests completed."
    )
    rerank_count: int = Field(
        default=0, description="Number of candidates successfully processed by the cloud reranker."
    )
    truncated: bool = Field(
        default=False, description="Whether provider or output limits truncated the result set."
    )
    incomplete_providers: list[str] = Field(
        default_factory=list,
        description="Providers that reported partial results due to timeouts or index limits.",
    )
    dropped_count: int = Field(
        default=0,
        description="Number of candidate hits dropped during scope filtering or compaction.",
    )
    estimated_tokens: int = Field(
        default=0,
        description="Estimated token count of the returned output payload.",
    )
    elapsed_ms: float = Field(default=0.0, description="Total elapsed search time in milliseconds.")
    returned_count: int = Field(
        default=0, description="Number of evidence hits returned to the caller."
    )


class QueryMetadata(BaseModel):
    """How the deterministic query planner interpreted the request."""

    original_query: str = Field(description="The caller's normalized natural-language query.")
    variants: list[str] = Field(
        default_factory=list, description="Planner variants used to improve recall across backends."
    )
    regex_source: str | None = Field(
        default=None,
        description="Validated regular-expression source when regex search is enabled.",
    )
    anchor_terms: list[str] = Field(
        default_factory=list, description="Exact terms used as high-signal lexical anchors."
    )
    qualifiers: dict[str, str] = Field(
        default_factory=dict, description="Recognized scope and provider-neutral query qualifiers."
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Planner or provider caveats relevant to interpreting the results.",
    )
    variant_kinds: list[str] = Field(
        default_factory=list,
        description="Planner role for each query variant, in the same order as variants.",
    )
    source_tokens: list[dict[str, str]] = Field(
        default_factory=list,
        description="Recognized source-language, API, symbol, or identifier tokens.",
    )
    concept_terms: list[str] = Field(
        default_factory=list,
        description="Normalized concept terms used for semantic and repository discovery.",
    )
    structural_kind: str | None = Field(
        default=None,
        description="Detected code structure, such as function, class, endpoint, or configuration.",
    )

    mode: str = Field(
        default="code",
        description="Search mode: 'code' (default), 'docs', 'discovery', or 'huggingface' (semantic Hub assets).",
    )
    backend_channels: list[str] = Field(
        default_factory=list, description="Backend channels selected automatically by the planner."
    )
    compiled_queries: dict[str, list[str]] = Field(
        default_factory=dict, description="Provider-specific query strings sent to each backend."
    )
    resolution_hints: dict[str, str] = Field(
        default_factory=dict,
        description="High-confidence hosted-entity hints used for documentation/repository resolution.",
    )


class CodeSearchResultType(BaseModel):
    """Structured output contract for ``code_search``."""

    query: str = Field(description="Normalized query submitted to code search.")
    outcome: Outcome = Field(
        description="Overall result state: ok, no_hit, partial, error, or skipped."
    )
    results: list[CodeSearchHit] = Field(description="Ranked code and documentation evidence hits.")
    repositories: list[RepoCandidate] = Field(
        description="Discovered repositories and their supporting proof metadata."
    )
    diagnostics: list[Diagnostic] = Field(
        description="Provider, planning, hydration, and fallback diagnostics."
    )
    stats: Stats = Field(description="Bounded execution statistics for this search.")
    query_metadata: QueryMetadata = Field(
        description="Planner interpretation, variants, scopes, and backend compilation details."
    )


_PATH_LANGUAGE = {
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".py": "Python",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".cpp": "C++",
    ".cc": "C++",
    ".c": "C",
    ".swift": "Swift",
    ".scala": "Scala",
    ".sql": "SQL",
    ".md": "Markdown",
    ".json": "JSON",
    ".yml": "YAML",
    ".yaml": "YAML",
}


class CodeSearchPublicSpan(BaseModel):
    """Match extent: absolute line/column (Sourcegraph/grep.app) or snippet-relative offsets (GitHub)."""

    line: int | None = Field(default=None, description="Absolute one-based line of the match.")
    column: int | None = Field(default=None, description="Zero-based column offset (Sourcegraph).")
    length: int | None = Field(default=None, description="Match length in characters.")
    start: int | None = Field(
        default=None, description="Snippet-relative start offset (GitHub indices)."
    )
    end: int | None = Field(
        default=None, description="Snippet-relative end offset (GitHub indices)."
    )
    line_offset: int | None = Field(
        default=None, description="Line offset within the snippet (GitHub)."
    )


class CodeSearchPublicMatchLines(BaseModel):
    """Line-precise coordinates for one text_matches entry, when known."""

    line_start: int | None = Field(
        default=None, description="First source line of this match or window."
    )
    line_end: int | None = Field(
        default=None, description="Last source line of this match or window."
    )
    spans: list[CodeSearchPublicSpan] = Field(
        default_factory=list,
        description="Exact match extents: absolute line/column (Sourcegraph/grep.app) or snippet-relative (GitHub).",
    )


class CodeSearchPublicSymbol(BaseModel):
    """Symbol hit contributed by a symbol-aware provider (Sourcegraph)."""

    name: str = Field(description="Symbol name.")
    kind: str | None = Field(default=None, description="Symbol kind, such as function or class.")
    container: str | None = Field(default=None, description="Enclosing symbol, when reported.")


class CodeSearchPublicFile(BaseModel):
    """One matched file with full provider evidence and agent-readiness status."""

    path: str | None = Field(default=None, description="Repository-relative file path.")
    language: str | None = Field(default=None, description="Detected or requested language.")
    url: str | None = Field(default=None, description="Canonical file URL.")
    sha: str | None = Field(default=None, description="Blob SHA or commit OID when known.")
    snippet: str | None = Field(default=None, description="Primary provider match context.")
    text_matches: list[str] = Field(default_factory=list, description="Provider match contexts.")
    match_lines: list[CodeSearchPublicMatchLines] = Field(
        default_factory=list,
        description="Parallel line coordinates and exact spans for text_matches.",
    )
    line_start: int | None = Field(default=None, description="First primary match line.")
    line_end: int | None = Field(default=None, description="Last primary match line.")
    symbols: list[CodeSearchPublicSymbol] = Field(default_factory=list)
    providers: list[str] = Field(default_factory=list)
    path_only: bool = Field(default=False)
    source_window_start: int | None = Field(default=None, description="First line of hydrated source window.")
    source_window_end: int | None = Field(default=None, description="Last line of hydrated source window.")
    full_source_chars: int | None = Field(default=None, description="Total chars of the full source file.")
    omitted_fragments: int = Field(default=0, description="Number of provider match fragments dropped by per-file caps.")
    agent_ready: bool = Field(default=False)
    agent_ready_fail_reasons: list[str] = Field(default_factory=list)

class CodeSearchPublicRepo(BaseModel):
    """Slim repository row for discovery mode."""

    repository: str = Field(description="owner/name repository.")
    url: str | None = Field(default=None)
    description: str | None = Field(default=None)
    language: str | None = Field(default=None)
    stars: int | None = Field(default=None)


class CodeSearchPublicAsset(BaseModel):
    """Semantic Hugging Face Hub asset returned by ``mode='huggingface'``."""

    asset_id: str
    asset_type: str
    url: str
    summary: str = ""
    semantic_score: float | None = None
    score_semantics: str = "provider_similarity"
    likes: int = 0
    downloads: int = 0
    param_count: int | None = None
    task: str | None = None
    license: str | None = None
    language: str | None = None
    last_modified: str | None = None
    api_rank: int | None = None


class CodeSearchPublicGroup(BaseModel):
    """Repository group containing all matched files under one owner/name."""

    repository: str = Field(description="owner/name repository.")
    owner: str | None = Field(default=None)
    repo: str | None = Field(default=None)
    files: list[CodeSearchPublicFile] = Field(default_factory=list)


class CodeSearchPublicHint(BaseModel):
    """Semantic, actionable agent guidance."""

    code: str = Field(description="Stable hint code.")
    message: str = Field(description="Human-readable explanation with a concrete next step.")


class CodeSearchPublicNext(BaseModel):
    """Machine-ready continuation to resolve exact lines or inspect a file."""

    action: str = Field(description="Continuation action.")
    tool: str = Field(description="Tool to call for this continuation.")
    query: dict[str, Any] = Field(default_factory=dict)
    why: str | None = Field(default=None)
    confidence: str | None = Field(default=None)


class CodeSearchPublicResult(BaseModel):
    """Structured MCP/CLI payload with full evidence and readiness metadata."""

    query: str = Field(description="Normalized query submitted to code search.")
    outcome: Outcome = Field(description="ok, no_hit, partial, error, or skipped.")
    incomplete_results: bool = Field(
        description="Whether provider or index diagnostics make the result set incomplete."
    )
    agent_ready_count: int = Field(description="Number of returned file rows that are agent-ready.")
    agent_ready_evidence_rate: float = Field(
        description="agent_ready_count divided by the returned file-row count, or 0 when empty."
    )
    results: list[CodeSearchPublicGroup] = Field(
        description="Repositories with matched files and provider text contexts."
    )
    repositories: list[CodeSearchPublicRepo] = Field(default_factory=list)
    hints: list[CodeSearchPublicHint] = Field(default_factory=list)
    next: list[CodeSearchPublicNext] = Field(default_factory=list)
    assets: list[CodeSearchPublicAsset] = Field(default_factory=list)


def _language_from_path(path: str | None) -> str | None:
    if not path or "." not in path.rsplit("/", 1)[-1]:
        return None
    suffix = "." + path.rsplit(".", 1)[-1].casefold()
    return _PATH_LANGUAGE.get(suffix)


def _spans_for_range(
    hit: CodeSearchHit, line_start: int | None, line_end: int | None
) -> list[CodeSearchPublicSpan]:
    """Build spans from provider match metadata without altering source text."""

    spans: list[CodeSearchPublicSpan] = []
    for span in hit.match_spans:
        if not isinstance(span, dict):
            continue
        line = span.get("line")
        if not isinstance(line, int):
            if isinstance(span.get("start"), int) and isinstance(span.get("end"), int):
                spans.append(
                    CodeSearchPublicSpan(
                        start=span["start"], end=span["end"], line_offset=span.get("fragment")
                    )
                )
            continue
        if line_start is not None and line < line_start:
            continue
        if line_end is not None and line > line_end:
            continue
        spans.append(
            CodeSearchPublicSpan(
                line=line,
                column=span.get("column") if isinstance(span.get("column"), int) else None,
                length=span.get("length") if isinstance(span.get("length"), int) else None,
            )
        )
    return spans


def _build_text_and_lines(
    hit: CodeSearchHit,
) -> list[tuple[str, CodeSearchPublicMatchLines]]:
    """Project every available provider context without truncation or deduplication."""

    pairs: list[tuple[str, CodeSearchPublicMatchLines]] = []
    for fragment in hit.fragments:
        if not isinstance(fragment.text, str) or not fragment.text.strip():
            continue
        offsets = fragment.match_metadata.get("offsets")
        spans: list[CodeSearchPublicSpan] = []
        if isinstance(offsets, list):
            for offset in offsets:
                if isinstance(offset, list) and len(offset) == 2:
                    spans.append(
                        CodeSearchPublicSpan(
                            line=fragment.line_start,
                            column=offset[0] if isinstance(offset[0], int) else None,
                            length=offset[1] if isinstance(offset[1], int) else None,
                        )
                    )
        if not spans:
            spans = _spans_for_range(hit, fragment.line_start, fragment.line_end)
        pairs.append(
            (
                fragment.text,
                CodeSearchPublicMatchLines(
                    line_start=fragment.line_start, line_end=fragment.line_end, spans=spans
                ),
            )
        )

    if isinstance(hit.snippet, str) and hit.snippet.strip():
        pairs.append(
            (
                hit.snippet,
                CodeSearchPublicMatchLines(
                    line_start=hit.line_start,
                    line_end=hit.line_end,
                    spans=_spans_for_range(hit, hit.line_start, hit.line_end),
                ),
            )
        )

    if isinstance(hit.hydrated_source, str) and hit.hydrated_source.strip():
        window_start = hit.source_metadata.get("source_window_start")
        window_end = hit.source_metadata.get("source_window_end")
        ws = window_start if isinstance(window_start, int) and window_start >= 1 else hit.line_start
        we = window_end if isinstance(window_end, int) and window_end >= 1 else hit.line_end
        pairs.append(
            (
                hit.hydrated_source,
                CodeSearchPublicMatchLines(
                    line_start=ws, line_end=we, spans=_spans_for_range(hit, ws, we)
                ),
            )
        )
    return pairs


def to_public_file(hit: CodeSearchHit, *, language: str | None = None) -> CodeSearchPublicFile:
    """Project one internal hit to a full structured file row with readiness status."""

    revision = hit.sha or hit.commit_oid or hit.location.revision
    detected = language or _language_from_path(hit.path)
    metadata_language = hit.source_metadata.get("language")
    if not detected and isinstance(metadata_language, str) and metadata_language.strip():
        detected = metadata_language.strip()
    symbols = [
        CodeSearchPublicSymbol(
            name=str(symbol.get("name") or ""),
            kind=symbol.get("kind") if isinstance(symbol.get("kind"), str) else None,
            container=(
                symbol.get("container")
                if isinstance(symbol.get("container"), str)
                else symbol.get("containerName")
                if isinstance(symbol.get("containerName"), str)
                else None
            ),
        )
        for symbol in hit.symbols
        if isinstance(symbol, dict) and symbol.get("name")
    ]
    pairs = _build_text_and_lines(hit)
    text_matches = [text for text, _ in pairs]
    match_lines = [lines for _, lines in pairs]
    primary_lines = match_lines[0] if match_lines else None
    line_start = hit.line_start or (primary_lines.line_start if primary_lines else None)
    line_end = hit.line_end or (primary_lines.line_end if primary_lines else None)
    providers: list[str] = []
    for provider in [hit.provider, *(hit.source_metadata.get("providers") or [])]:
        if isinstance(provider, str) and provider.strip() and provider not in providers:
            providers.append(provider)
    agent_ready, fail_reasons = assess_candidate_readiness(hit)
    return CodeSearchPublicFile(
        path=hit.path,
        language=detected,
        url=hit.url or None,
        sha=revision,
        snippet=text_matches[0] if text_matches else None,
        text_matches=text_matches,
        match_lines=match_lines,
        line_start=line_start,
        line_end=line_end,
        symbols=symbols,
        providers=providers,
        path_only=not text_matches and not match_lines,
        source_window_start=hit.source_metadata.get("source_window_start"),
        source_window_end=hit.source_metadata.get("source_window_end"),
        full_source_chars=hit.source_metadata.get("full_source_chars"),
        omitted_fragments=max(0, int(hit.source_metadata.get("omitted_fragments") or 0)),
        agent_ready=agent_ready,
        agent_ready_fail_reasons=[] if agent_ready else fail_reasons,
    )


def _build_hints(result: CodeSearchResultType, plan: Any | None) -> list[CodeSearchPublicHint]:
    """Semantic, actionable hints inspired by Octocode's warning model."""

    hints: list[CodeSearchPublicHint] = []
    has_incomplete = any(diag.failure_kind == "incomplete_index" for diag in result.diagnostics)
    has_auth_fail = any(diag.failure_kind == "auth" for diag in result.diagnostics)
    has_results = bool(result.results)
    has_scoped_qualifiers = bool(plan and plan.qualifiers)
    is_regex = bool(plan and plan.regex_source and plan.local_regex is None)

    if has_auth_fail:
        hints.append(
            CodeSearchPublicHint(
                code="provider_unavailable",
                message="GitHub code search requires GITHUB_TOKEN or GH_TOKEN. Set one to enable GitHub results; other providers still searched.",
            )
        )
    if has_incomplete:
        hints.append(
            CodeSearchPublicHint(
                code="incomplete_index",
                message="Some providers returned incomplete index results. Empty or partial results may be a false negative — retry or narrow scope.",
            )
        )
    if not has_results and has_scoped_qualifiers:
        hints.append(
            CodeSearchPublicHint(
                code="scoped_zero_unproven",
                message="No results for a scoped query. Treat as unproven absence: verify the repo/path exists, then retry with broader filters.",
            )
        )
    if not has_results and is_regex:
        hints.append(
            CodeSearchPublicHint(
                code="regex_invalid",
                message="Regex query returned no results. Verify regex syntax or try a literal/symbol search.",
            )
        )
    if not has_results and not has_scoped_qualifiers and not is_regex:
        hints.append(
            CodeSearchPublicHint(
                code="narrow_scope",
                message="No code matches found. Try specific function/class identifier names, or use mode='docs' or mode='discovery'.",
            )
        )
    return hints


def _build_next(result: CodeSearchResultType, plan: Any | None) -> list[CodeSearchPublicNext]:
    """Build continuation records for matched files with their exact anchors."""

    nexts: list[CodeSearchPublicNext] = []
    if not result.results:
        return nexts
    anchor = ""
    if plan and plan.anchor_terms:
        anchor = plan.anchor_terms[0]
    elif plan and plan.variants:
        anchor = plan.variants[0]
    if not anchor:
        return nexts
    for hit in result.results:
        url = hit.url
        if not url:
            continue
        if hit.repository and hit.path:
            nexts.append(
                CodeSearchPublicNext(
                    action="explore",
                    tool="code_fetch",
                    query={
                        "repository": hit.repository,
                        "path": hit.path,
                    },
                    why="Explore the matched file on the repository's current main snapshot.",
                    confidence="high",
                )
            )
        else:
            nexts.append(
                CodeSearchPublicNext(
                    action="get_lines",
                    tool="get_content",
                    query={"url": url, "focus_query": anchor},
                    why="Fetch the file with focus_query to resolve exact file:line anchors.",
                    confidence="low",
                )
            )
    return nexts


def to_public_result(
    result: CodeSearchResultType,
    *,
    language: str | None = None,
    plan: Any | None = None,
) -> CodeSearchPublicResult:
    """Group hits by repository and path while preserving all structured evidence."""

    repositories: list[CodeSearchPublicRepo] = []
    if result.query_metadata.mode == "discovery":
        for repo in result.repositories:
            repositories.append(
                CodeSearchPublicRepo(
                    repository=repo.name_with_owner,
                    url=repo.url,
                    description=repo.description,
                    language=repo.language,
                    stars=repo.stars or None,
                )
            )

    groups: list[CodeSearchPublicGroup] = []
    by_repo: dict[str, CodeSearchPublicGroup] = {}
    by_file: dict[str, dict[str, CodeSearchPublicFile]] = {}
    best_score: dict[str, float] = {}
    for hit in result.results:
        repository = hit.repository or "unknown"
        group = by_repo.get(repository)
        if group is None:
            owner, _, repo_name = repository.partition("/")
            group = CodeSearchPublicGroup(
                repository=repository, owner=owner or None, repo=repo_name or None
            )
            by_repo[repository] = group
            by_file[repository] = {}
            best_score[repository] = hit.score or 0.0
            groups.append(group)
        else:
            best_score[repository] = max(best_score.get(repository, 0.0), hit.score or 0.0)

        path_key = (hit.path or "").casefold()
        existing = by_file[repository].get(path_key)
        file_entry = to_public_file(hit, language=language)
        if existing is None:
            by_file[repository][path_key] = file_entry
            group.files.append(file_entry)
            continue

        existing.text_matches.extend(file_entry.text_matches)
        existing.match_lines.extend(file_entry.match_lines)
        for symbol in file_entry.symbols:
            if symbol not in existing.symbols:
                existing.symbols.append(symbol)
        for provider in file_entry.providers:
            if provider not in existing.providers:
                existing.providers.append(provider)
        existing.sha = existing.sha or file_entry.sha
        existing.url = existing.url or file_entry.url
        existing.language = existing.language or file_entry.language
        existing.snippet = existing.snippet or file_entry.snippet
        existing.line_start = existing.line_start or file_entry.line_start
        existing.line_end = existing.line_end or file_entry.line_end
        existing.path_only = existing.path_only and file_entry.path_only
        if file_entry.agent_ready:
            existing.agent_ready = True
            existing.agent_ready_fail_reasons = []
        elif not existing.agent_ready:
            existing.agent_ready_fail_reasons.extend(file_entry.agent_ready_fail_reasons)
        existing.omitted_fragments += file_entry.omitted_fragments
        if file_entry.source_window_start is not None and existing.source_window_start is None:
            existing.source_window_start = file_entry.source_window_start
        if file_entry.source_window_end is not None and existing.source_window_end is None:
            existing.source_window_end = file_entry.source_window_end
        if file_entry.full_source_chars is not None and existing.full_source_chars is None:
            existing.full_source_chars = file_entry.full_source_chars

    groups.sort(key=lambda group: -best_score.get(group.repository, 0.0))
    file_count = sum(len(group.files) for group in groups)
    agent_ready_count = sum(
        1 for group in groups for file_entry in group.files if file_entry.agent_ready
    )
    incomplete_results = bool(
        result.outcome == "partial"
        or result.stats.incomplete_providers
        or any(d.failure_kind == "incomplete_index" for d in result.diagnostics)
    )
    assets = []
    for hit in result.results:
        if hit.provider != "huggingface":
            continue
        metadata = hit.source_metadata
        assets.append(
            CodeSearchPublicAsset(
                asset_id=str(metadata.get("asset_id") or hit.repository or ""),
                asset_type=str(metadata.get("asset_type") or "unknown"),
                url=hit.url,
                summary=hit.snippet or "",
                semantic_score=metadata.get("semantic_score"),
                score_semantics=str(metadata.get("score_semantics") or "provider_similarity"),
                likes=int(metadata.get("likes") or 0),
                downloads=int(metadata.get("downloads") or 0),
                param_count=metadata.get("param_count"),
                task=metadata.get("task"),
                license=metadata.get("license"),
                language=metadata.get("language"),
                last_modified=metadata.get("last_modified"),
                api_rank=metadata.get("api_rank"),
            )
        )
    return CodeSearchPublicResult(
        query=result.query,
        outcome=result.outcome,
        incomplete_results=incomplete_results,
        agent_ready_count=agent_ready_count,
        agent_ready_evidence_rate=agent_ready_count / file_count if file_count else 0.0,
        results=groups,
        repositories=repositories,
        assets=assets,
        hints=_build_hints(result, plan),
        next=_build_next(result, plan),
    )


@dataclass(frozen=True, slots=True)
class SearchBudget:
    """Local work/output budgets; these are not provider quota accounting.

    GitHub's /search/code endpoint enforces a hard 10 requests/minute limit;
    ``max_code_search_requests`` defaults to 8 to leave headroom under that cap.
    """

    max_repositories: int = 25
    max_code_search_requests: int = 8
    max_results_per_search: int = 100
    max_hydrate_files: int = 25
    max_hydrated_chars_per_file: int = 200_000
    max_query_variants: int = 3
    max_rerank_candidates: int = 100
    max_rerank_results: int = 50


@dataclass(frozen=True, slots=True)
class CodeSearchRequest:
    """Internal normalized request passed to adapters."""

    query: str
    research_goal: str = ""
    repositories: tuple[str, ...] = ()
    language: str | None = None
    path: str | None = None
    filename: str | None = None
    extension: str | None = None
    regexp: bool = False
    deep: bool = False
    max_results: int = 100
    repo_name: str | None = None
    library_name: str | None = None
    topic: str | None = None
    mode: str = "code"
    huggingface_type: str = "both"
    huggingface_sort_by: str = "similarity"
    huggingface_hybrid: bool = False
    huggingface_min_likes: int = 0
    huggingface_min_downloads: int = 0
    huggingface_task: str | None = None
    huggingface_license: str | None = None
    huggingface_language: str | None = None
    huggingface_modified_after: str | None = None
    huggingface_min_param_count: int = 0
    huggingface_max_param_count: int | None = None
    budget: SearchBudget = field(default_factory=SearchBudget)


@dataclass(slots=True)
class ProviderResponse:
    """Internal adapter result preserving partial failures."""

    provider: str
    hits: list[CodeSearchHit] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    request_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def outcome(self) -> Outcome:
        meaningful = [
            diagnostic
            for diagnostic in self.diagnostics
            if diagnostic.outcome in {"partial", "error"}
        ]
        if self.hits:
            return "partial" if meaningful else "ok"
        if any(diagnostic.outcome == "partial" for diagnostic in meaningful):
            return "partial"
        if any(diagnostic.outcome == "error" for diagnostic in meaningful):
            return "error"
        return "no_hit"


def utc_now_iso() -> str:
    """Return a compact UTC timestamp for provider metadata."""

    return datetime.now().astimezone().isoformat()
