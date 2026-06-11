"""Evaluation case models and deterministic metric helpers."""

from .cases import CandidateSet, EvalCase, ExpectedToolCall
from .judges import (
    judge_argument_correctness,
    judge_ranking_quality,
    judge_source_usefulness,
    judge_tool_choice_correct,
)
from .metrics import (
    expected_tool_called,
    forbidden_tool_not_called,
    latency_within_budget,
    mrr_at_k,
    ndcg_at_k,
    top_k_domain_hit,
)
from .runner import MCPEVAL_AVAILABLE, run_dataset, run_eval_case

__all__ = [
    "CandidateSet",
    "EvalCase",
    "ExpectedToolCall",
    "expected_tool_called",
    "forbidden_tool_not_called",
    "latency_within_budget",
    "mrr_at_k",
    "ndcg_at_k",
    "top_k_domain_hit",
    "judge_tool_choice_correct",
    "judge_argument_correctness",
    "judge_source_usefulness",
    "judge_ranking_quality",
    "run_eval_case",
    "run_dataset",
    "MCPEVAL_AVAILABLE",
]
