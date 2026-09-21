"""Internal pipeline currency for the web_search data layer.

Engine testimony, adapter envelopes, scored rows, and run results are frozen
dataclasses: they are constructed inside this package, so unknown constructor
kwargs fail loudly at the parser instead of being silently dropped the way
Pydantic models do. Pydantic remains only at the MCP wire boundary in
``models.py``.
"""

from dataclasses import dataclass
from typing import Literal

from ..models import FilterStats, ProviderWarning, SearchStopReason

AnswerKind = Literal["featured_snippet", "paa", "knowledge", "llm", "forum"]
SourceKind = Literal["forum", "video", "social", "official", "news", "docs", "other"]
FailureKind = Literal[
    "parse_error",
    "rate_limited",
    "quota_exhausted",
    "bot_challenge",
    "permission_denied",
    "budget_exhausted",
    "timeout",
    "upstream_4xx",
    "upstream_5xx",
    "query_truncated",
    "empty_result",
]


@dataclass(frozen=True, slots=True)
class QueryIntegrity:
    """What the engine did with the query text that was sent."""

    sent_query: str
    detected_query: str | None = None
    truncated: bool = False
    spelling: str | None = None
    result_count: int | None = None


@dataclass(frozen=True, slots=True)
class EngineFailure:
    """Typed engine-call failure detected before or instead of page hits."""

    kind: FailureKind
    message: str
    code: str | None = None
    retryable: bool = False
    retry_after: float | None = None


@dataclass(frozen=True, slots=True)
class SearchHit:
    """Engine testimony for one result: what the engine said, nothing derived."""

    title: str
    url: str
    snippet: str
    domain: str
    adapter: str
    engine_rank: int | None = None
    provider_score: float | None = None
    source_name: str | None = None
    source_kind: SourceKind | None = None
    published: str | None = None
    highlights: tuple[str, ...] = ()
    source_engines: tuple[str, ...] = ()
    origin_adapters: tuple[str, ...] = ()
    answer_kind: AnswerKind | None = None


@dataclass(frozen=True, slots=True)
class EngineCall:
    """One engine invocation: ordered hits plus expansion and integrity.

    Empty ``hits`` with ``failure is None`` is a real zero-hit result; empty
    ``hits`` with a failure means the call itself failed.
    """

    adapter: str
    query: str
    hits: tuple[SearchHit, ...] = ()
    expansion: tuple[str, ...] = ()
    integrity: QueryIntegrity | None = None
    failure: EngineFailure | None = None


@dataclass(frozen=True, slots=True)
class ScoredHit:
    """Pipeline view of one hit: the engine testimony plus pipeline-computed scores."""

    hit: SearchHit
    retrieval_rrf_score: float | None = None
    bi_encoder_score: float | None = None
    cross_encoder_score: float | None = None
    rankllm_score: float | None = None
    recency_score: float | None = None
    diversity_penalty: float | None = None
    final_score: float | None = None
    final_rank: int | None = None
    evidence_final: float | None = None
    evidence_semantic: float | None = None
    evidence_lexical: float | None = None
    evidence_consensus: int | None = None
    freshness_signal: Literal["fresh", "dated", "unknown"] | None = None
    citation_id: str | None = None
    fetch_hint_query: str | None = None
    providers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AdaptiveRound:
    """One adaptive retrieval wave and the decision that followed it."""

    index: int
    branch_start: int
    branch_count: int
    queries: tuple[str, ...]
    candidate_count: int
    new_url_count: int
    domain_count: int
    provider_failure_count: int
    decision: Literal["search", "finish"]
    reason: str


@dataclass(frozen=True, slots=True)
class SearchRunResult:
    """Internal run result; replaces the legacy Pydantic web search response."""

    query: str
    hits: tuple[ScoredHit, ...]
    total_results: int = 0
    providers_used: tuple[str, ...] = ()
    warnings: tuple[ProviderWarning, ...] = ()
    intent: str | None = None
    filter_stats: FilterStats | None = None
    rounds: int = 0
    stop_reason: SearchStopReason | None = None
    synthesis: str | None = None
