"""Deterministic eval metrics for tool routing and ranked candidates."""

from __future__ import annotations

import math
from urllib.parse import urlparse


def expected_tool_called(tool_calls: list[dict[str, object]], tool_name: str) -> float:
    return 1.0 if _tool_called(tool_calls, tool_name) else 0.0


def forbidden_tool_not_called(
    tool_calls: list[dict[str, object]], tool_name: str
) -> float:
    return 0.0 if _tool_called(tool_calls, tool_name) else 1.0


def latency_within_budget(latency_ms: float, budget_ms: float) -> float:
    return 1.0 if latency_ms <= budget_ms else 0.0


def mrr_at_k(candidates: list[dict[str, object]], expected_domain: str, k: int) -> float:
    for index, candidate in enumerate(candidates[:k], start=1):
        if _candidate_domain_matches(candidate, expected_domain):
            return 1.0 / index
    return 0.0


def ndcg_at_k(candidates: list[dict[str, object]], k: int) -> float:
    gains = [_binary_relevance(candidate) for candidate in candidates[:k]]
    ideal = sorted(gains, reverse=True)
    ideal_dcg = _dcg(ideal)
    if ideal_dcg == 0:
        return 0.0
    return _dcg(gains) / ideal_dcg


def top_k_domain_hit(
    candidates: list[dict[str, object]], expected_domain: str, k: int
) -> float:
    return 1.0 if any(
        _candidate_domain_matches(candidate, expected_domain)
        for candidate in candidates[:k]
    ) else 0.0


def _tool_called(tool_calls: list[dict[str, object]], tool_name: str) -> bool:
    return any(call.get("tool_name") == tool_name for call in tool_calls)


def _candidate_domain_matches(candidate: dict[str, object], expected_domain: str) -> bool:
    domain = candidate.get("domain")
    if isinstance(domain, str) and domain == expected_domain:
        return True

    url = candidate.get("url")
    if not isinstance(url, str):
        return False
    return urlparse(url).netloc == expected_domain


def _binary_relevance(candidate: dict[str, object]) -> float:
    relevance = candidate.get("relevance", candidate.get("score", 0))
    if isinstance(relevance, bool):
        return 1.0 if relevance else 0.0
    if isinstance(relevance, int | float):
        return 1.0 if relevance > 0 else 0.0
    return 0.0


def _dcg(gains: list[float]) -> float:
    return sum(gain / math.log2(index + 1) for index, gain in enumerate(gains, start=1))
