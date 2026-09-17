"""Shared rerank contracts and embedding context models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from pydantic import BaseModel, Field, field_validator

from ..search.types import ScoredHit, SearchHit
from ..utils.url_canonicalize import canonicalize_url


@dataclass(frozen=True, slots=True)
class RerankCandidate:
    """Document prepared for a rerank provider."""

    index: int
    document: str


@dataclass(frozen=True, slots=True)
class RerankResult:
    """Ranked document index and provider relevance score."""

    index: int
    relevance_score: float


RerankStageName = Literal["bi_encoder", "cross_encoder", "rankllm", "mmr_fallback"]
RerankTerminalStage = Literal[
    "rrf",
    "bi_encoder",
    "cross_encoder",
    "rankllm",
    "mmr_fallback",
]
RerankOverflowStage = Literal["rankllm", "mmr_fallback", "cross", "rrf"]


class RerankOverflowItem(BaseModel):
    """A candidate left outside the returned page and its last stage."""

    model_config = {"arbitrary_types_allowed": True}
    stage: RerankOverflowStage
    result: SearchHit


class RerankStageSummary(BaseModel):
    stage: RerankStageName
    provider: str | None = None
    model: str | None = None
    input_count: int
    output_count: int
    duration_ms: float
    status: Literal["success", "partial", "skipped", "fallback_success", "failed_open"]
    error_type: str | None = None
    max_score: float | None = None
    avg_score: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    instruction_present: bool | None = None
    instruction_length: int | None = None
    query_type_hint: str | None = None
    attempted_passes: int | None = None
    valid_passes: int | None = None
    failed_passes: int | None = None


# =============================================================================
# Embedding Context Models (for rerank -> Qdrant embedding reuse)
# =============================================================================


class CandidateEmbedding(BaseModel):
    """A single candidate's precomputed dense embedding and source text.

    Stored in a RerankEmbeddingContext for lookup by URL across pipeline stages.
    """

    url: str = Field(description="Result URL (dedup/identity key)")
    text: str = Field(description="Text that was embedded (f'{title}\\n{snippet}')")
    dense: list[float] = Field(description="384-dimensional dense embedding vector")

    @field_validator("url")
    @classmethod
    def _canonicalize_identity_url(cls, value: str) -> str:
        return canonicalize_url(value)

    model_config = {"frozen": True}


class RerankEmbeddingContext(BaseModel):
    """Query embedding + per-candidate embeddings produced by bi-encoder stage.

    Carried through the rerank pipeline so MMR diversity and downstream
    consumers (e.g. Qdrant index) can reuse the already-computed vectors.
    """

    query_embedding: list[float] = Field(description="384-dimensional query embedding vector")
    candidates: list[CandidateEmbedding] = Field(
        description="Per-candidate dense embeddings, indexed by url"
    )

    def find(self, url: str) -> CandidateEmbedding | None:
        lookup_url = canonicalize_url(url)
        for candidate in self.candidates:
            if canonicalize_url(candidate.url) == lookup_url:
                return candidate
        return None


class RerankOutput(BaseModel):
    """Return type for rerank_results carrying final results + embedding context.

    Consumers that only need results can access ``.results`` and ignore the context.
    """

    model_config = {"arbitrary_types_allowed": True}
    results: list[ScoredHit] = Field(description="Final reranked and diversified results.")
    embedding_context: RerankEmbeddingContext | None = Field(
        default=None,
        description="Per-candidate embeddings reusable by downstream stages.",
    )
    provider: str | None = Field(
        default=None,
        description="Reranker provider that produced the final ordering.",
    )
    model: str | None = Field(
        default=None,
        description="Model used by the reranker provider.",
    )
    stage_summaries: list[RerankStageSummary] = Field(
        default_factory=list,
        description="Observable summaries for each rerank stage.",
    )
    overflow_items: list[RerankOverflowItem] = Field(
        default_factory=list,
        description="Candidates left outside the returned slate, grouped by terminal stage.",
    )
    terminal_stage: RerankTerminalStage = "rrf"
    funnel_counts: dict[str, int] = Field(default_factory=dict)


# =============================================================================
CROSS_ENCODER_INPUT_LIMIT: Final[int] = 100
RANKLLM_INPUT_LIMIT: Final[int] = 30
FINAL_RESULT_LIMIT: Final[int] = 15


@dataclass(frozen=True, slots=True)
class RankedStageOutcome:
    candidates: list[ScoredHit]
    provider: str
    model: str | None
    stage_name: str
    input_count: int
    output_count: int
    duration_seconds: float
    relevance_scores: list[float]
    max_score: float
    avg_score: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: Exception | None = None
    full_candidates: list[ScoredHit] | None = None
    attempted_passes: int = 0
    valid_passes: int = 0
    failed_passes: int = 0
