"""Typed contracts for the multi-provider code-search tool."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

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
        description="Search mode: 'code' (default), 'docs', or 'discovery' (repo discovery).",
    )
    backend_channels: list[str] = Field(
        default_factory=list, description="Backend channels selected automatically by the planner."
    )
    compiled_queries: dict[str, list[str]] = Field(
        default_factory=dict, description="Provider-specific query strings sent to each backend."
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


class CodeSearchPublicHit(BaseModel):
    """Agent-facing hit. Mirrors GitHub MCP / Octocode / ``gh search code``."""

    repository: str | None = Field(default=None, description="owner/name repository.")
    path: str | None = Field(default=None, description="Repository-relative file path.")
    language: str | None = Field(default=None, description="Detected or requested language.")
    line_start: int | None = Field(default=None, description="One-based first source line.")
    line_end: int | None = Field(default=None, description="One-based last source line.")
    text_matches: list[str] = Field(
        default_factory=list,
        description="Code windows / fragments, same role as GitHub text_matches.",
    )
    url: str | None = Field(default=None, description="Canonical file or evidence URL.")
    sha: str | None = Field(default=None, description="Blob SHA or commit OID when known.")


class CodeSearchPublicRepo(BaseModel):
    """Slim repository row for discovery mode (gh search repos / GitHub MCP)."""

    repository: str = Field(description="owner/name repository.")
    url: str | None = Field(default=None, description="Canonical repository URL.")
    description: str | None = Field(default=None, description="Repository description.")
    language: str | None = Field(default=None, description="Primary language.")
    stars: int | None = Field(default=None, description="Stargazer count when known.")


class CodeSearchPublicResult(BaseModel):
    """Public MCP/CLI payload: path + code first, ranking telemetry omitted."""

    query: str = Field(description="Normalized query submitted to code search.")
    outcome: Outcome = Field(description="ok, no_hit, partial, error, or skipped.")
    results: list[CodeSearchPublicHit] = Field(
        description="Ranked file hits with text_matches, grouped implicitly by path."
    )
    repositories: list[CodeSearchPublicRepo] = Field(
        default_factory=list,
        description="Discovery-mode repository candidates only.",
    )
    truncated: bool = Field(
        default=False, description="True when more hits existed than were returned."
    )
    more_results: int = Field(
        default=0, description="Hits dropped after ranking to keep the payload bounded."
    )
    warnings: list[str] = Field(
        default_factory=list, description="Short caller-facing caveats, not provider dumps."
    )


def _language_from_path(path: str | None) -> str | None:
    if not path or "." not in path.rsplit("/", 1)[-1]:
        return None
    suffix = "." + path.rsplit(".", 1)[-1].casefold()
    return _PATH_LANGUAGE.get(suffix)


def _text_matches_for_hit(hit: CodeSearchHit) -> list[str]:
    """Prefer hydrated windows, then provider fragments, then the short snippet."""

    matches: list[str] = []
    if hit.hydrated_source and hit.hydrated_source.strip():
        matches.append(hit.hydrated_source)
    for fragment in hit.fragments:
        text = fragment.text.strip()
        if text and text not in matches:
            matches.append(text)
    snippet = (hit.snippet or "").strip()
    if snippet and snippet not in matches:
        matches.append(snippet)
    return matches


def to_public_hit(hit: CodeSearchHit, *, language: str | None = None) -> CodeSearchPublicHit:
    """Project an internal hit to the GitHub-MCP-style public row."""

    revision = hit.sha or hit.commit_oid or hit.location.revision
    line_start = hit.line_start or hit.location.line_start
    line_end = hit.line_end or hit.location.line_end
    window_start = hit.source_metadata.get("source_window_start")
    window_end = hit.source_metadata.get("source_window_end")
    if isinstance(window_start, int) and window_start >= 1:
        line_start = line_start or window_start
    if isinstance(window_end, int) and window_end >= 1:
        line_end = line_end or window_end
    detected = language or _language_from_path(hit.path)
    metadata_language = hit.source_metadata.get("language")
    if not detected and isinstance(metadata_language, str) and metadata_language.strip():
        detected = metadata_language.strip()
    return CodeSearchPublicHit(
        repository=hit.repository,
        path=hit.path,
        language=detected,
        line_start=line_start,
        line_end=line_end,
        text_matches=_text_matches_for_hit(hit),
        url=hit.url or None,
        sha=revision,
    )


def to_public_result(
    result: CodeSearchResultType,
    *,
    language: str | None = None,
) -> CodeSearchPublicResult:
    """Strip ranking/provider telemetry from the MCP-facing payload."""

    warnings: list[str] = []
    seen: set[str] = set()
    incomplete_noted = False
    for diagnostic in result.diagnostics:
        message = (diagnostic.message or "").strip()
        if not message or message in seen:
            continue
        if diagnostic.failure_kind == "incomplete_index":
            if incomplete_noted:
                continue
            incomplete_noted = True
            message = "Some providers returned incomplete index results."
        seen.add(message)
        warnings.append(message)
        if len(warnings) >= 5:
            break
    repositories: list[CodeSearchPublicRepo] = []
    if result.query_metadata.mode == "discovery":
        for repo in result.repositories[:15]:
            repositories.append(
                CodeSearchPublicRepo(
                    repository=repo.name_with_owner,
                    url=repo.url,
                    description=repo.description,
                    language=repo.language,
                    stars=repo.stars or None,
                )
            )
    return CodeSearchPublicResult(
        query=result.query,
        outcome=result.outcome,
        results=[to_public_hit(hit, language=language) for hit in result.results],
        repositories=repositories,
        truncated=result.stats.truncated,
        more_results=result.stats.dropped_count,
        warnings=warnings,
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
    max_output_chars: int = 2_000_000
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
