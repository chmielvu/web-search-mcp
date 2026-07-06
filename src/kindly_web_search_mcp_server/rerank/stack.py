"""Rerank stack mode selection."""

from __future__ import annotations

from dataclasses import dataclass

VALID_RERANK_STACK_MODES = {"bi_cross", "bi_llm", "bi_cross_llm"}


@dataclass(frozen=True, slots=True)
class RerankStackPlan:
    mode: str
    use_cross_encoder: bool
    use_llm_reranker: bool
    stage_order: tuple[str, ...]


def normalize_rerank_stack_mode(raw_mode: str) -> str:
    mode = raw_mode.strip().lower()
    if mode not in VALID_RERANK_STACK_MODES:
        allowed = ", ".join(sorted(VALID_RERANK_STACK_MODES))
        raise ValueError(f"Invalid rerank stack mode: {raw_mode!r}. Expected one of: {allowed}.")
    return mode


def build_rerank_stack_plan(raw_mode: str) -> RerankStackPlan:
    mode = normalize_rerank_stack_mode(raw_mode)
    if mode == "bi_cross":
        return RerankStackPlan(
            mode=mode,
            use_cross_encoder=True,
            use_llm_reranker=False,
            stage_order=("bi_encoder", "cross_encoder", "diversity"),
        )
    if mode == "bi_llm":
        return RerankStackPlan(
            mode=mode,
            use_cross_encoder=False,
            use_llm_reranker=True,
            stage_order=("bi_encoder", "llm_reranker", "diversity"),
        )
    return RerankStackPlan(
        mode=mode,
        use_cross_encoder=True,
        use_llm_reranker=True,
        stage_order=("bi_encoder", "cross_encoder", "llm_reranker", "diversity"),
    )
