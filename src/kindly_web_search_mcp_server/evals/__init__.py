"""Evaluation case models and deterministic metric helpers."""

from .cases import CandidateSet, EvalCase, ExpectedToolCall
from .metrics import (
    expected_tool_called,
    forbidden_tool_not_called,
    latency_within_budget,
    mrr_at_k,
    ndcg_at_k,
    top_k_domain_hit,
)

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
]
