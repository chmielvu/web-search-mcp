"""Agent-facing contracts for the disposable refined code-search prototype.

The provider adapters use lightweight internal records, but the boundary is
deliberately explicit: an agent should not have to reverse-engineer provider
metadata to decide whether a result is a candidate, a verified implementation,
or merely an indexed mention.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MatchRole = Literal[
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
]
EvidenceLevel = Literal["candidate", "search_match", "hydrated", "verified"]
ProviderOutcome = Literal["ok", "empty", "partial", "failed", "skipped"]


class SearchScope(BaseModel):
    """Provider-neutral scope, following gh-grep/octogrep-style filters."""

    model_config = ConfigDict(extra="forbid")

    repositories: list[str] = Field(
        default_factory=list, description="Canonical owner/repo values or GitHub URLs."
    )
    organizations: list[str] = Field(default_factory=list)
    users: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    path: str | None = Field(
        default=None, description="Repository path prefix or provider path qualifier."
    )
    filenames: list[str] = Field(default_factory=list)
    extensions: list[str] = Field(default_factory=list)
    include: list[str] = Field(
        default_factory=list, description="Path regular expressions included after retrieval."
    )
    exclude: list[str] = Field(
        default_factory=list, description="Path regular expressions excluded after retrieval."
    )
    ref: str | None = Field(
        default=None, description="Branch, tag, or commit to search when supported."
    )

    @field_validator("repositories")
    @classmethod
    def normalize_repositories(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            value = value.strip().rstrip("/")
            if value.lower().startswith("https://github.com/"):
                value = value[len("https://github.com/") :]
            if value.lower().startswith("http://github.com/"):
                value = value[len("http://github.com/") :]
            if value.endswith(".git"):
                value = value[:-4]
            parts = value.split("/")
            if len(parts) != 2 or not all(parts):
                raise ValueError(f"repository must be owner/repo or a GitHub URL: {value!r}")
            if value not in normalized:
                normalized.append(value)
        return normalized


class MatchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_sensitive: bool = False
    whole_word: bool = False


class DiscoveryOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topics: list[str] = Field(default_factory=list)
    owners: list[str] = Field(default_factory=list)
    language: str | None = None
    min_stars: int | None = Field(default=None, ge=0)
    sort: Literal["best", "stars", "updated", "forks"] = "best"
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=10, ge=1, le=100)
    prove: bool = True


class DocsOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_name: str | None = None
    library_name: str | None = None
    topic: str | None = None


class SearchInput(BaseModel):
    """Validated agent request for public code or documentation search.

    ``scope``, ``match``, and ``discovery`` are deliberately nested so an agent
    can compose requests without encoding provider-specific query syntax.
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1,
        max_length=2_000,
        description="Implementation, symbol, usage, or code-pattern request.",
        examples=["retry middleware exponential backoff", "fetchWithRetry"],
    )
    scope: SearchScope = Field(default_factory=SearchScope)
    match: MatchSpec = Field(default_factory=MatchSpec)
    roles: list[MatchRole] = Field(
        default_factory=list, description="Optional post-hydration evidence-role filters."
    )
    discovery: DiscoveryOptions | None = None
    docs: DocsOptions | None = None
    limit: int = Field(default=5, ge=1, le=50, description="Maximum final evidence results.")
    context_lines: int = Field(
        default=6, ge=0, le=30, description="Source lines surrounding the matched line."
    )

    @field_validator("query", mode="before")
    @classmethod
    def strip_query(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("query must be a non-blank string")
        return value.strip()

    @model_validator(mode="after")
    def validate_fields(self) -> SearchInput:
        if self.scope.organizations or self.scope.users:
            raise ValueError(
                "structured organization/user scopes are not compiled yet; use repositories"
            )
        if (
            len(self.scope.languages) > 1
            or len(self.scope.filenames) > 1
            or len(self.scope.extensions) > 1
        ):
            raise ValueError(
                "this prototype accepts one language, filename, and extension filter per request"
            )
        if self.discovery and self.discovery.page != 1:
            raise ValueError("repository discovery pagination is not implemented in this prototype")
        return self

    # Internal adapter properties keep provider orchestration readable while
    # leaving the public schema provider-neutral and nested.
    @property
    def repositories(self) -> list[str]:
        return self.scope.repositories

    @property
    def language(self) -> str | None:
        return self.scope.languages[0] if self.scope.languages else None

    @property
    def path(self) -> str | None:
        return self.scope.path

    @property
    def filename(self) -> str | None:
        return self.scope.filenames[0] if self.scope.filenames else None

    @property
    def extension(self) -> str | None:
        return self.scope.extensions[0] if self.scope.extensions else None

    @property
    def include_paths(self) -> list[str]:
        return self.scope.include

    @property
    def exclude_paths(self) -> list[str]:
        return self.scope.exclude

    @property
    def match_role(self) -> MatchRole | None:
        return self.roles[0] if self.roles else None

    @property
    def discover_repositories(self) -> bool:
        return self.discovery is not None

    @property
    def result_limit(self) -> int:
        return self.limit

    @property
    def repo_name(self) -> str | None:
        return self.docs.repo_name if self.docs else None

    @property
    def library_name(self) -> str | None:
        return self.docs.library_name if self.docs else None

    @property
    def topic(self) -> str | None:
        return self.docs.topic if self.docs else None


class QueryVariant(BaseModel):
    """One exact provider query produced by the planner."""

    id: str
    kind: Literal["primary", "compact", "identifier", "regex", "proof", "discovery"] = "primary"
    provider: str
    query: str
    filters: dict[str, Any] = Field(default_factory=dict)


class MatchSpan(BaseModel):
    """Provider-normalized match location."""

    line: int | None = None
    line_end: int | None = None
    column_start: int | None = None
    column_end: int | None = None
    offset: int | None = None
    length: int | None = None
    text: str | None = None


class SourceWindow(BaseModel):
    """Bounded reusable source context around evidence."""

    text: str
    start_line: int | None = None
    end_line: int | None = None
    match_line: int | None = None
    truncated: bool = False


class SymbolEvidence(BaseModel):
    name: str
    kind: str | None = None
    container: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    url: str | None = None


class RepositoryCandidate(BaseModel):
    """A discovered repository separated from proven file evidence."""

    repository: str
    url: str | None = None
    description: str | None = None
    readme_preview: str | None = None
    topics: list[str] = Field(default_factory=list)
    language: str | None = None
    stars: int = 0
    pushed_at: str | None = None
    discovery_score: float | None = None
    discovery_queries: list[str] = Field(default_factory=list)
    proof_status: Literal["not_requested", "verified", "empty", "failed"] = "not_requested"
    proof_hits: int = 0
    proof_paths: list[str] = Field(default_factory=list)
    proof_query: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_runtime_candidate(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        item = dict(value)
        if not item.get("repository"):
            item["repository"] = item.get("name_with_owner")
        if "discovery_score" not in item:
            item["discovery_score"] = item.get("relevance")
        if "proof_status" not in item:
            if item.get("proof_requested"):
                item["proof_status"] = "verified" if item.get("verified") else "empty"
            else:
                item["proof_status"] = "verified" if item.get("verified") else "not_requested"
        return item


class RevisionEvidence(BaseModel):
    """Identity needed to reproduce a source match later."""

    ref: str | None = None
    commit_oid: str | None = None
    blob_oid: str | None = None
    pinned_url: str | None = None


class CodeSearchResult(BaseModel):
    """One agent-consumable code/documentation evidence item."""

    repository: str | None = None
    path: str | None = None
    url: str
    language: str | None = None
    primary_provider: str | None = None
    providers: list[str] = Field(default_factory=list)
    matched_variant_ids: list[str] = Field(default_factory=list)
    revision: RevisionEvidence = Field(default_factory=RevisionEvidence)
    evidence_level: EvidenceLevel = "search_match"
    verified: bool = False
    role: MatchRole = "code"
    matches: list[MatchSpan] = Field(default_factory=list)
    symbols: list[SymbolEvidence] = Field(default_factory=list)
    source_window: SourceWindow | None = None
    snippet: str | None = None
    score: float | None = None
    score_components: dict[str, Any] = Field(default_factory=dict)
    rank_reasons: list[str] = Field(default_factory=list)
    source_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_runtime_hit(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        item = dict(value)
        provider = item.get("provider")
        item.setdefault("providers", [provider] if provider else [])
        commit = item.get("hydrated_commit") or item.get("revision")
        item.setdefault("primary_provider", provider)
        metadata = item.get("source_metadata", {})
        item.setdefault(
            "matched_variant_ids",
            metadata.get("query_variants")
            or ([metadata["query_variant"]] if metadata.get("query_variant") else []),
        )
        if not isinstance(item.get("revision"), dict):
            item["revision"] = {
                "ref": item.get("revision"),
                "commit_oid": item.get("hydrated_commit") or commit,
                "blob_oid": item.get("blob_sha"),
                "pinned_url": item.get("url") if commit else None,
            }
        item.setdefault("verified", bool(item.get("verified")))
        item.setdefault("evidence_level", "verified" if item.get("verified") else "search_match")
        role = item.get("match_role") or item.get("match_kind") or "code"
        item["role"] = role if role in MatchRole.__args__ else "code"
        if not item.get("providers") and provider:
            item["providers"] = [provider]
        if not item.get("rank_reasons"):
            item["rank_reasons"] = item.get("reasons", [])
        if item.get("symbols"):
            item["symbols"] = [
                {
                    **symbol,
                    "container": symbol.get("container") or symbol.get("containerName"),
                }
                for symbol in item["symbols"]
            ]
        if not item.get("matches"):
            spans = item.get("match_spans") or []
            lines = item.get("match_lines") or []
            item["matches"] = spans or [{"line": line} for line in lines]
            if not item["matches"] and item.get("match_line"):
                item["matches"] = [{"line": item["match_line"]}]
        if not item.get("source_window") and item.get("snippet"):
            item["source_window"] = {
                "text": item["snippet"],
                "start_line": item.get("line_start"),
                "end_line": item.get("line_end"),
                "match_line": item.get("match_line"),
                "truncated": False,
            }
        window = item.get("source_window")
        if isinstance(window, dict) and len(window.get("text", "")) >= 1800:
            window["truncated"] = True
        return item


class ProviderStatus(BaseModel):
    provider: str
    outcome: ProviderOutcome = "ok"
    query: str | None = None
    variant_id: str | None = None
    raw_count: int | None = None
    returned_count: int = 0
    incomplete: bool = False
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_runtime_status(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        item = dict(value)
        raw_status = item.pop("status", None)
        message = item.get("message") or raw_status
        if "outcome" not in item:
            lowered = str(raw_status or "").casefold()
            if "skip" in lowered:
                outcome = "skipped"
            elif any(token in lowered for token in ("error", "failed", "http 4", "http 5")):
                outcome = "failed"
            elif "no hit" in lowered or "empty" in lowered:
                outcome = "empty"
            else:
                outcome = "ok"
            item["outcome"] = outcome
        if message is not None:
            item["message"] = message
        if item.get("raw_count") is None:
            item["raw_count"] = item.get(
                "total",
                item.get("match_count", item.get("matchCount")),
            )
        if not item.get("returned_count"):
            item["returned_count"] = item.get("count", 0)
        if item.get("incomplete") is None:
            item["incomplete"] = bool(item.get("limit_hit") or item.get("incomplete_results"))
        known = set(cls.model_fields)
        item["details"] = {
            **item.get("details", {}),
            **{key: value for key, value in item.items() if key not in known},
        }
        return item


class SearchStats(BaseModel):
    raw_result_count: int = 0
    unique_result_count: int = 0
    verified_result_count: int = 0
    returned_count: int = 0
    candidate_count: int = 0
    elapsed_ms: float = 0.0
    truncated: bool = False
    truncation_reasons: list[str] = Field(default_factory=list)


class SearchResponse(BaseModel):
    """Stable serialized response returned to an agent."""

    query: str
    outcome: Literal["ok", "empty", "partial", "failed", "skipped"] = "ok"
    query_interpretation: dict[str, Any] = Field(default_factory=dict)
    variants: list[QueryVariant] = Field(default_factory=list)
    matches: list[CodeSearchResult] = Field(default_factory=list)
    repositories: list[RepositoryCandidate] = Field(default_factory=list)
    providers: list[ProviderStatus] = Field(default_factory=list)
    stats: SearchStats = Field(default_factory=SearchStats)
    warnings: list[str] = Field(default_factory=list)
    elapsed_ms: float = 0.0

    @model_validator(mode="before")
    @classmethod
    def normalize_runtime_response(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        item = dict(value)
        if "matches" not in item and item.get("results") is not None:
            item["matches"] = item["results"]
        if "repositories" not in item and item.get("repository_candidates") is not None:
            item["repositories"] = item["repository_candidates"]
        if "providers" not in item and item.get("provider_statuses") is not None:
            item["providers"] = item["provider_statuses"]
        if "variants" not in item:
            plan = item.get("query_plan") or {}
            item["variants"] = [
                {
                    "id": variant.get("id", f"variant-{index}"),
                    "kind": "regex"
                    if variant.get("regex")
                    else ("primary" if index == 0 else "compact"),
                    "provider": provider,
                    "query": variant.get(provider, ""),
                }
                for index, variant in enumerate(plan.get("variants", []))
                for provider in ("github", "sourcegraph", "grepapp")
                if variant.get(provider)
            ]
        if "outcome" not in item:
            matches = item.get("matches") or []
            providers = item.get("providers") or []
            repositories = item.get("repositories") or []
            raw_provider_statuses = providers
            provider_failed = any(
                "error" in str(status).casefold()
                or "failed" in str(status).casefold()
                or "http 4" in str(status).casefold()
                or "http 5" in str(status).casefold()
                for provider in raw_provider_statuses
                for status in [provider.get("status", provider.get("outcome", ""))]
                if isinstance(provider, dict)
            )
            if matches or repositories:
                item["outcome"] = "partial" if provider_failed else "ok"
            elif providers:
                item["outcome"] = "empty"
            else:
                item["outcome"] = "skipped"
        return item

    @model_validator(mode="after")
    def derive_stats(self) -> SearchResponse:
        provider_returned = sum(provider.returned_count for provider in self.providers)
        if not self.stats.raw_result_count:
            self.stats.raw_result_count = provider_returned or len(self.matches)
        if not self.stats.unique_result_count:
            self.stats.unique_result_count = len(self.matches)
        if not self.stats.returned_count:
            self.stats.returned_count = len(self.matches)
        if not self.stats.verified_result_count:
            self.stats.verified_result_count = sum(item.verified for item in self.matches)
        if not self.stats.candidate_count:
            self.stats.candidate_count = len(self.repositories)
        if not self.elapsed_ms and self.stats.elapsed_ms:
            self.elapsed_ms = self.stats.elapsed_ms
        if not self.stats.elapsed_ms and self.elapsed_ms:
            self.stats.elapsed_ms = self.elapsed_ms
        if not self.stats.truncated:
            self.stats.truncated = any(provider.incomplete for provider in self.providers)
        return self
