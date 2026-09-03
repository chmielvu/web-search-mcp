"""Validated search boundaries and immutable execution records."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence

import httpx
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated

from ..models import ProviderWarning, WebSearchResponse, WebSearchResult
from .options import SearchOptions
from .understanding.models import QueryUnderstandingResult

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ContractModel(BaseModel):
    """Strict immutable model used at untrusted boundaries."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class BranchRole(str, Enum):
    ORIGINAL = "original"
    FREE = "free"
    SERP1 = "serp1"
    SERP2 = "serp2"
    SEMANTIC_TAVILY = "semantic_tavily"
    SEMANTIC_EXA = "semantic_exa"


class QueryBranch(ContractModel):
    role: BranchRole
    query: NonBlank
    provider_names: tuple[str, ...]
    why: str = ""
    support_terms: tuple[str, ...] = ()
    max_results: int = Field(ge=1, le=100)


class WebSearchRequest(ContractModel):
    query: NonBlank
    queries: tuple[NonBlank, ...] = ()
    research_goal: NonBlank
    num_results: Literal[15] = 15
    rewrite: bool = True
    options: SearchOptions = Field(default_factory=SearchOptions)
    reranking_instructions: str | None = None
    # Internal-only post-processing inputs (not part of the public wire
    # contract; consumed by run_search_core).
    include_undated: bool | None = None
    domain_boost: tuple[str, ...] = ()
    pre_warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchPlan:
    normalized_query: str
    relevance_query: str
    understanding: QueryUnderstandingResult
    options: SearchOptions
    provider_arguments: Mapping[str, Mapping[str, Any]]
    branches: tuple[QueryBranch, ...]
    policy_version: str
    rewrite_queries: tuple[
        str, ...
    ] = ()  # 5 planner rewrites (k1, k2, k3, neural, specialized); distinct from `branches` which is the 6-branch topology
    seed_queries: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        normalized_query: str,
        relevance_query: str,
        understanding: QueryUnderstandingResult,
        options: SearchOptions,
        provider_arguments: Mapping[str, Mapping[str, Any]],
        branches: Sequence[QueryBranch],
        policy_version: str,
        rewrite_queries: Sequence[str] = (),  # 5 planner rewrites (k1, k2, k3, neural, specialized)
        seed_queries: Sequence[str] = (),
    ) -> "SearchPlan":
        copied = {
            name: MappingProxyType(dict(values)) for name, values in provider_arguments.items()
        }
        return cls(
            normalized_query=normalized_query,
            relevance_query=relevance_query,
            understanding=understanding,
            options=options,
            provider_arguments=MappingProxyType(copied),
            branches=tuple(branches),
            policy_version=policy_version,
            rewrite_queries=tuple(rewrite_queries),
            seed_queries=tuple(seed_queries),
        )


@dataclass(frozen=True, slots=True)
class ProviderRankedResults:
    branch_index: int
    branch_role: BranchRole
    provider_name: str
    results: tuple[WebSearchResult, ...]


@dataclass(frozen=True, slots=True)
class BranchOutcome:
    branch: QueryBranch
    attempted_provider_names: tuple[str, ...] = ()
    skipped_provider_names: tuple[str, ...] = ()
    results: tuple[WebSearchResult, ...] = ()
    warnings: tuple[ProviderWarning, ...] = ()
    elapsed_seconds: float = 0.0
    provider_calls: tuple[dict[str, Any], ...] = ()
    provider_ranked_results: tuple[ProviderRankedResults, ...] = ()


@dataclass
class DiagnosticsCollector:
    """Mutable per-run diagnostics gathered across plan / retrieve / rank."""

    enrichment: dict[str, Any] | None = None
    rewrite_metadata: dict[str, Any] | None = None
    intent: str | None = None
    understanding_confidence: float | None = None
    branch_results: list[dict[str, Any]] = field(default_factory=list)
    merge_counts: dict[str, int] = field(default_factory=dict)
    rerank_stage_summaries: list[dict[str, Any]] = field(default_factory=list)
    phase_timings: dict[str, float] = field(default_factory=dict)
    query_embedding: list[float] | None = None
    merged_candidates: list[Any] = field(default_factory=list)
    candidate_embeddings: list[dict[str, Any]] = field(default_factory=list)
    provider_result_rows: list[dict[str, Any]] = field(default_factory=list)
    query_variant_rows: list[dict[str, Any]] = field(default_factory=list)
    query_transform_rows: list[dict[str, Any]] = field(default_factory=list)
    total_latency_ms: float | None = None
    query_shaping: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    run_key: str
    status: Literal["success", "error", "cancelled"]
    request: WebSearchRequest
    plan: SearchPlan | None
    outcomes: tuple[BranchOutcome, ...]
    response: WebSearchResponse | None
    error_summary: str | None
    rerank_metadata: Mapping[str, Any]
    timings: Mapping[str, float]
    tool_call_id: str | None
    session_id: str | None


@dataclass(slots=True)
class SearchRun:
    request: WebSearchRequest
    http_client: httpx.AsyncClient
    run_key: str
    tool_call_id: str | None = None
    session_id: str | None = None
    progress: Any | None = None
    plan: SearchPlan | None = None
    outcomes: tuple[BranchOutcome, ...] = ()
    rerank_metadata: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    status: Literal["running", "success", "error", "cancelled"] = "running"
    response: WebSearchResponse | None = None
    error_summary: str | None = None
    diagnostics: DiagnosticsCollector = field(default_factory=DiagnosticsCollector)
    schedule_judges: bool = True

    def _transition(
        self,
        status: Literal["success", "error", "cancelled"],
        *,
        response: WebSearchResponse | None = None,
        error_summary: str | None = None,
    ) -> None:
        if self.status != "running":
            raise RuntimeError(f"SearchRun already completed with status {self.status!r}")
        self.status = status
        self.response = response
        self.error_summary = error_summary

    def succeed(self, response: WebSearchResponse) -> None:
        self._transition("success", response=response)

    def fail(self, error_summary: str) -> None:
        self._transition("error", error_summary=error_summary)

    def cancel(self, error_summary: str) -> None:
        self._transition("cancelled", error_summary=error_summary)

    def snapshot(self) -> SearchOutcome:
        if self.status == "running":
            raise RuntimeError("Cannot snapshot a running SearchRun")
        return SearchOutcome(
            run_key=self.run_key,
            status=self.status,
            request=self.request,
            plan=self.plan,
            outcomes=tuple(self.outcomes),
            response=self.response.model_copy(deep=True) if self.response is not None else None,
            error_summary=self.error_summary,
            rerank_metadata=MappingProxyType(dict(self.rerank_metadata)),
            timings=MappingProxyType(dict(self.timings)),
            tool_call_id=self.tool_call_id,
            session_id=self.session_id,
        )
