"""Rerank eligibility bypass policy (observable).

Per joint plan Task 3.2: returns typed decision for low count, exact literal,
navigational domain, degraded health, harmful class. Emits rerank.* events.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ..utils.observability import emit_observability_event
from ..settings import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RerankDecision:
    should_rerank: bool
    reason: str
    query_type: str
    candidate_count: int
    engine_health: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


def _is_exact_literal(query: str) -> bool:
    q = query.strip()
    if len(q) < 8:
        return False
    # quoted strings, tracebacks, error codes, file paths with line, hashes
    if re.search(r'["\'][^"\']{5,}[\"\']', q):
        return True
    if re.search(r"(Traceback|FileNotFound|Exception|Error:|\.py:\d+|0x[0-9a-fA-F]{8,})", q):
        return True
    if re.search(r"\b[0-9a-f]{32,}\b", q):  # long hex/hash
        return True
    return False


def _is_navigational_exact_domain(query: str) -> bool:
    q = query.lower()
    if re.search(r"\bsite:[a-z0-9.-]+\.[a-z]{2,}\b", q):
        return True
    if re.search(r"\bgithub\.com/[\w-]+/[\w-]+", q) and len(q.split()) <= 6:
        return True
    return False


def get_rerank_engine_health() -> dict[str, Any]:
    """Stub; in real would query provider_health or circuit from engines."""
    # For now always healthy unless settings force; tests monkeypatch.
    return {"status": "healthy", "cooldown_remaining": 0}


def classify_query_risk(query: str) -> str:
    """Stub for eval-proven harmful class. Tests override."""
    q = query.lower()
    if any(k in q for k in ["exploit", "buffer overflow", "rce", "sql injection", "0day"]):
        return "harmful"
    return "normal"


def decide_rerank(
    *,
    query: str,
    candidate_count: int,
    top_k: int | None = None,
    query_type_hint: str | None = None,
) -> RerankDecision:
    """Core policy decision. Emits rerank.eligibility always, bypass/completed as appropriate."""
    top_k = top_k or 10
    qtype = query_type_hint or "general"

    # Emit eligibility upfront (plan requires rerank.eligibility)
    emit_observability_event(
        logger,
        "rerank.eligibility",
        query=query[:200],
        candidate_count=candidate_count,
        top_k=top_k,
        configured_provider=settings.rerank_provider,
    )

    # 1. low count
    if candidate_count <= top_k:
        d = RerankDecision(
            should_rerank=False,
            reason="low_candidate_count",
            query_type=qtype,
            candidate_count=candidate_count,
        )
        emit_observability_event(
            logger, "rerank.bypassed", reason=d.reason, query=query[:200], candidate_count=candidate_count
        )
        return d

    # 2. exact literal (quoted errors, traces)
    if _is_exact_literal(query):
        d = RerankDecision(
            should_rerank=False,
            reason="exact_literal",
            query_type="literal",
            candidate_count=candidate_count,
        )
        emit_observability_event(
            logger, "rerank.bypassed", reason=d.reason, query=query[:200]
        )
        return d

    # 3. navigational exact domain
    if _is_navigational_exact_domain(query):
        d = RerankDecision(
            should_rerank=False,
            reason="navigational_exact_domain",
            query_type="navigational",
            candidate_count=candidate_count,
        )
        emit_observability_event(
            logger, "rerank.bypassed", reason=d.reason, query=query[:200]
        )
        return d

    # 4. degraded health
    health = get_rerank_engine_health()
    if health.get("status") == "degraded":
        d = RerankDecision(
            should_rerank=False,
            reason="degraded_engine_health",
            query_type=qtype,
            candidate_count=candidate_count,
            engine_health=health,
        )
        emit_observability_event(
            logger, "rerank.bypassed", reason=d.reason, query=query[:200], engine_health=health
        )
        return d

    # 5. harmful from classifier (eval-proven)
    risk = classify_query_risk(query)
    if risk == "harmful":
        d = RerankDecision(
            should_rerank=False,
            reason="harmful_query_class",
            query_type="harmful",
            candidate_count=candidate_count,
        )
        emit_observability_event(
            logger, "rerank.bypassed", reason=d.reason, query=query[:200]
        )
        return d

    # eligible
    d = RerankDecision(
        should_rerank=True,
        reason="eligible",
        query_type=qtype,
        candidate_count=candidate_count,
        engine_health=health,
        details={"entity_overlap_enabled": bool(getattr(settings, "rerank_entity_overlap_enabled", False))},
    )
    emit_observability_event(
        logger,
        "rerank.engine_selected",
        engine_id=settings.rerank_provider,
        query=query[:200],
        candidate_count=candidate_count,
    )
    return d
