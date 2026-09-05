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


class CodeSearchHit(BaseModel):
    """Clean provider-neutral code search hit."""

    repository: str | None = Field(
        default=None, description="Repository containing the evidence, when identified."
    )
    path: str | None = Field(default=None, description="Repository-relative file path.")
    url: str = Field(default="", description="Canonical URL for the evidence or source file.")
    provider: str = Field(description="Backend that returned this evidence item.")
    sha: str | None = Field(
        default=None, description="Provider blob SHA or revision identifier, when available."
    )
    commit_oid: str | None = Field(default=None, description="Exact commit OID, when available.")
    query_variant: str | None = Field(
        default=None, description="Planner or provider query variant."
    )
    search_rank: int | None = Field(
        default=None, description="Provider-native rank before cross-provider ranking."
    )
    result_kind: ResultKind = Field(
        default="code_match",
        description="Evidence kind: code_match, semantic_page, documentation, repository.",
    )
    evidence_role: str | None = Field(
        default=None, description="Role of evidence: definition, callsite, test, documentation."
    )
    title: str | None = Field(
        default=None, description="Human-readable title supplied by provider."
    )
    published_date: str | None = Field(default=None, description="Publication or update date.")

    # Canonical bounded source window (populated after hydration & windowing)
    source_window: str | None = Field(
        default=None, description="Clean bounded source window (max 100 lines)."
    )
    line_start: int | None = Field(
        default=None, description="One-based start line of the source window."
    )
    line_end: int | None = Field(
        default=None, description="One-based end line of the source window."
    )
    match_lines: list[int] = Field(
        default_factory=list, description="One-based absolute lines matching query terms."
    )
    symbols: list[dict[str, Any]] = Field(
        default_factory=list, description="Structured symbols associated with the matched code."
    )

    # Internal scoring & metadata (excluded from public serialization)
    score: float | None = Field(default=None, description="Composite relevance/quality score.")
    score_components: dict[str, Any] = Field(
        default_factory=dict, description="Component scores for explainability."
    )
    reasons: list[str] = Field(default_factory=list, description="Human-readable ranking reasons.")
    source_metadata: dict[str, Any] = Field(
        default_factory=dict, description="Provider and hydration metadata."
    )
    location: LocationMetadata = Field(
        default_factory=LocationMetadata, description="Location precision metadata."
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
        match_data_available = bool(
            hit.source_window
            or hit.match_lines
            or (isinstance(hit.line_start, int) and hit.line_start >= 1)
        )
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
    rerank_provider: str | None = Field(default=None, exclude=True)
    rerank_model: str | None = Field(default=None, exclude=True)
    rerank_status: str | None = Field(default=None, exclude=True)
    rerank_duration_ms: float | None = Field(default=None, exclude=True)
    rerank_input_count: int | None = Field(default=None, exclude=True)
    rerank_output_count: int | None = Field(default=None, exclude=True)
    rerank_diagnostic_outcome: str | None = Field(default=None, exclude=True)
    rerank_diagnostic_message: str | None = Field(default=None, exclude=True)
    rerank_payload: dict[str, Any] = Field(default_factory=dict, exclude=True)
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
    provider_summaries: list[dict[str, Any]] = Field(
        default_factory=list,
        exclude=True,
        description="Internal provider summaries retained for typed analytics only.",
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

    source_window: str | None = Field(default=None, description="Matched source code window.")
    line_start: int | None = Field(default=None, description="First line of the source window.")
    line_end: int | None = Field(default=None, description="Last line of the source window.")

    symbols: list[CodeSearchPublicSymbol] = Field(default_factory=list)
    providers: list[str] = Field(default_factory=list)
    path_only: bool = Field(default=False)

    full_source_chars: int | None = Field(
        default=None, description="Total chars of the full source file."
    )
    omitted_fragments: int = Field(
        default=0, description="Number of provider match fragments dropped."
    )
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
    """Machine-ready continuation to search a repository snapshot or inspect a file."""

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

    source_window = (
        hit.source_window
        if isinstance(hit.source_window, str) and hit.source_window.strip()
        else None
    )
    line_start = hit.line_start
    line_end = hit.line_end
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
        source_window=source_window,
        line_start=line_start,
        line_end=line_end,
        symbols=symbols,
        providers=providers,
        path_only=not bool(source_window),
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
    """Build continuation records for repository searches or exact file fetches."""

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
    seen_repositories: set[str] = set()
    for hit in result.results:
        if hit.repository:
            if hit.repository in seen_repositories:
                continue
            seen_repositories.add(hit.repository)
            nexts.append(
                CodeSearchPublicNext(
                    action="search",
                    tool="code_fetch",
                    query={
                        "repository": hit.repository,
                        "query": anchor,
                    },
                    why=(
                        "Search all files in the matched repository snapshot for the anchor; "
                        "use a hit path for a focused read."
                    ),
                    confidence="high",
                )
            )
        elif hit.url:
            nexts.append(
                CodeSearchPublicNext(
                    action="get_lines",
                    tool="fetch",
                    query={"url": hit.url, "focus_query": anchor},
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
            best_score[repository] = hit.score or 0.0
            groups.append(group)
        else:
            best_score[repository] = max(best_score.get(repository, 0.0), hit.score or 0.0)

        file_entry = to_public_file(hit, language=language)
        group.files.append(file_entry)

    for group in groups:
        for file_entry in group.files:
            if file_entry.agent_ready:
                file_entry.agent_ready_fail_reasons = []
            else:
                file_entry.agent_ready_fail_reasons = sorted(
                    dict.fromkeys(file_entry.agent_ready_fail_reasons)
                )

    groups.sort(key=lambda group: -best_score.get(group.repository, 0.0))
    file_count = sum(len(group.files) for group in groups)
    agent_ready_count = sum(
        1 for group in groups for file_entry in group.files if file_entry.agent_ready
    )
    incomplete_results = bool(
        result.outcome == "partial"
        or result.stats.incomplete_providers
        or any(
            d.failure_kind in {"incomplete_index", "rate_limit", "budget"}
            for d in result.diagnostics
        )
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
                summary=hit.source_window or "",
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
