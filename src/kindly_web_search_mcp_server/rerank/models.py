"""Shared rerank contracts and embedding context models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Literal

from pydantic import BaseModel, Field


@dataclass(frozen=True, slots=True)
class RerankCandidate:
    """Document prepared for a rerank provider."""

    index: int
    document: str


@dataclass(frozen=True, slots=True)
class RerankResult:
    """Ranked document index and provider score."""

    index: int
    score: float


class RerankStageSummary(BaseModel):
    stage: Literal["bi_encoder", "cross_encoder", "rankllm"]
    provider: str | None = None
    model: str | None = None
    input_count: int
    output_count: int
    duration_ms: float
    status: Literal["success", "skipped", "fallback_success", "failed_open"]
    error_type: str | None = None
    max_score: float | None = None
    avg_score: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    score_threshold: float | None = None
    alpha_blend: float | None = None
    instruction_present: bool | None = None
    instruction_length: int | None = None
    query_type_hint: str | None = None
    entity_overlap_enabled: bool | None = None
    payload_json: dict[str, Any] = Field(default_factory=dict)


class RerankLLMOutput(BaseModel):
    """Structured response schema for listwise LLM reranking."""

    ranked_candidate_ids: list[int] = Field(
        default_factory=list,
        description="Ordered candidate ids from most relevant to least relevant",
    )

    model_config = {"frozen": True}


class RerankProvider(Protocol):
    """Async rerank provider boundary."""

    provider_id: str

    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        *,
        model: str | None = None,
        instruction: str | None = None,
    ) -> list[RerankResult]:
        """Return ranked candidate indexes with relevance scores."""
        ...


# =============================================================================
# Embedding Context Models (for rerank -> Qdrant embedding reuse)
# =============================================================================


class CandidateEmbedding(BaseModel):
    """A single candidate's precomputed dense embedding and source text.

    Stored in a RerankEmbeddingContext for lookup by URL across pipeline stages.
    """

    url: str = Field(description="Result URL (dedup/identity key)")
    text: str = Field(description="Text that was embedded (f'{title}\\n{snippet}')")
    dense: list[float] = Field(description="786-dimensional dense embedding vector")

    model_config = {"frozen": True}


class RerankEmbeddingContext(BaseModel):
    """Query embedding + per-candidate embeddings produced by bi-encoder stage.

    Carried through the rerank pipeline so MMR diversity and downstream
    consumers (e.g. Qdrant index) can reuse the already-computed vectors.
    """

    query_embedding: list[float] = Field(description="786-dimensional query embedding vector")
    candidates: list[CandidateEmbedding] = Field(
        description="Per-candidate dense embeddings, indexed by url"
    )

    def find(self, url: str) -> CandidateEmbedding | None:
        for c in self.candidates:
            if c.url == url:
                return c
        return None


class RerankOutput(BaseModel):
    """Return type for rerank_results carrying final results + embedding context.

    Consumers that only need results can access ``.results`` and ignore the context.
    """

    results: list[Any] = Field(
        description="Final reranked and diversified results (WebSearchResult objects)"
    )
    embedding_context: RerankEmbeddingContext | None = Field(
        default=None,
        description="Per-candidate embeddings for reuse in downstream stages (e.g. Qdrant)",
    )
    provider: str | None = Field(
        default=None,
        description="Reranker provider that produced the final results (e.g. cohere_fast, voyage, groq)",
    )
    model: str | None = Field(
        default=None,
        description="Model used by the reranker provider",
    )
    stage_summaries: tuple[RerankStageSummary, ...] = ()
    funnel_counts: dict[str, int] = Field(default_factory=dict)
